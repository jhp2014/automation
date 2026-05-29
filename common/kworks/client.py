"""KWorks UI driver shared by the server upload and capture upload jobs.

The class :class:`KworksClient` wraps a Playwright sync ``Page`` and exposes
the high-level KWorks workflow: login, navigate to a task detail view, type
a comment, upload one or more files, and submit. The two production scripts
``worker_upload.py`` (multi-file + folder-name comment) and
``ac_upload_kworks.py`` (single-file + optional comment) collapse to the same
sequence of calls against this client.

Browser / context lifecycle is NOT owned by this class — callers (or a future
``common/browser.py`` helper) must launch and tear down Playwright themselves.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List, Optional

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from . import selectors as S


class KworksClient:
    """High-level KWorks workflow driver bound to a single Playwright page."""

    def __init__(
        self,
        page: Page,
        *,
        logger: logging.Logger,
        timeout_ms: int = 30000,
        settle_ms: int = 600,
        banner_max_rounds: int = 6,
        new_input_timeout_ms: int = 2500,
        per_file_upload_timeout_ms: int = 30000,
        per_file_max_retries: int = 3,
        retry_sleep_ms: int = 600,
        retry_backoff_ms: int = 400,
    ) -> None:
        """Bind the client to a Playwright page and timing parameters.

        Args:
            page: A Playwright sync ``Page`` already pointing somewhere (the
                concrete URL is set later via :meth:`login`).
            logger: Logger used for all status messages. Caller must construct
                it via :func:`common.logging.get_logger`.
            timeout_ms: Default ``wait_for`` / ``wait_for_url`` timeout used
                for ordinary navigation steps.
            settle_ms: Short pause (ms) after DOM-mutating actions.
            banner_max_rounds: Max passes when stacking-banner closer retries.
            new_input_timeout_ms: How long to wait for a fresh ``input[type=
                file]`` element to appear after clicking the upload label.
            per_file_upload_timeout_ms: How long to wait for the ``file_nm``
                count to increase after a file is set.
            per_file_max_retries: Per-file upload retry budget.
            retry_sleep_ms: Base backoff before retry.
            retry_backoff_ms: Linear backoff increment per retry attempt.
        """
        self._page = page
        self._log = logger
        self._timeout_ms = timeout_ms
        self._settle_ms = settle_ms
        self._banner_max_rounds = banner_max_rounds
        self._new_input_timeout_ms = new_input_timeout_ms
        self._per_file_upload_timeout_ms = per_file_upload_timeout_ms
        self._default_max_retries = per_file_max_retries
        self._retry_sleep_ms = retry_sleep_ms
        self._retry_backoff_ms = retry_backoff_ms

    # ---------------------------------------------------------------- public

    def login(self, url: str, user_id: str, user_pw: str) -> None:
        """Navigate to ``url`` and authenticate if a login form is shown.

        If already authenticated (no login form), the method waits briefly
        for the ``main.act`` landing URL and returns. If the login form is
        present, credentials are filled and the form is submitted, then the
        method waits for ``main.act`` to load.

        Args:
            url: KWorks entry URL.
            user_id: Login id.
            user_pw: Login password.

        Raises:
            playwright.sync_api.TimeoutError: If the post-login navigation
                to ``main.act`` does not complete within ``timeout_ms``.
        """
        self._log.info("[STEP] Open URL: %s", url)
        self._page.goto(url, wait_until="domcontentloaded")
        self._page.wait_for_timeout(self._settle_ms)

        self._close_banners()

        if self._is_login_page():
            self._log.info("[STEP] Login page detected -> logging in")
            self._page.locator(S.SEL_USER_ID).first.fill(user_id)
            self._page.locator(S.SEL_PASSWORD).first.fill(user_pw)
            self._page.locator(S.SEL_LOGIN_BUTTON).first.click()
            self._page.wait_for_url("**/main.act*", timeout=self._timeout_ms)
            self._log.info("[OK] Logged in and landed on main.act")
        else:
            try:
                self._page.wait_for_url("**/main.act*", timeout=10000)
            except PlaywrightTimeoutError:
                pass
            self._log.info("[OK] Not a login page (maybe already logged in)")

    def open_task_detail(self, target_title: str) -> Locator:
        """Open the "자세히 보기" detail view for a task matched by title.

        Sequence: navigate to 전체 업무 → close banners → ensure left filters
        are fully selected → expand section toggles → find the task row by
        ``target_title`` → click "자세히 보기" → wait for the active comment
        form to become visible.

        Args:
            target_title: Exact task title (matched against
                ``span.js-task-title-nm[mouseover-text=...]``).

        Returns:
            A :class:`Locator` for the active comment form (the value to pass
            to :meth:`type_comment`, :meth:`upload_files`, :meth:`submit`).

        Raises:
            RuntimeError: If a required filter cannot be selected.
            playwright.sync_api.TimeoutError: If any DOM wait exceeds
                ``timeout_ms``.
        """
        self._log.info("[STEP] Go to '전체 업무'")
        self._page.locator(S.SEL_TASK_NAV).first.wait_for(
            state="visible", timeout=self._timeout_ms
        )
        self._close_banners()
        self._page.locator(S.SEL_TASK_NAV).first.click()

        self._page.locator(S.SEL_ALL_COLLECT_VIEW).first.wait_for(
            state="visible", timeout=self._timeout_ms
        )
        self._page.locator(S.SEL_ALL_TASK_FILTER).first.wait_for(
            state="visible", timeout=self._timeout_ms
        )
        self._page.locator(S.SEL_TASK_TITLE_NM).first.wait_for(
            state="visible", timeout=self._timeout_ms
        )
        self._close_banners()
        self._page.wait_for_timeout(self._settle_ms)

        self._ensure_all_filters()

        self._log.info("[STEP] Expand section toggles (best-effort)")
        self._expand_toggles()

        self._page.locator(S.SEL_ALL_COLLECT_VIEW).first.wait_for(
            state="visible", timeout=self._timeout_ms
        )
        self._page.locator(S.SEL_TASK_TITLE_NM).first.wait_for(
            state="visible", timeout=self._timeout_ms
        )
        self._close_banners()
        self._page.wait_for_timeout(200)

        self._log.info("[STEP] Find target title: %s", target_title)
        title_span = self._page.locator(
            S.sel_task_title_by_mouseover(target_title)
        ).first
        title_span.wait_for(state="visible", timeout=self._timeout_ms)

        row = title_span.locator(
            "xpath=ancestor::div[contains(@class,'task-title')]"
        ).first
        row.hover()
        self._page.wait_for_timeout(200)

        detail_btn = row.locator("span.span-work-show-detail").first
        detail_btn.wait_for(state="visible", timeout=self._timeout_ms)

        self._close_banners()
        self._log.info("[STEP] Click '자세히 보기'")
        detail_btn.click()

        self._page.wait_for_timeout(self._settle_ms)
        self._close_banners()

        form = self._page.locator(S.SEL_ACTIVE_FORM).first
        form.wait_for(state="visible", timeout=self._timeout_ms)
        self._log.info("[OK] Entered detail view (active comment form visible)")
        return form

    def type_comment(self, form: Locator, text: str) -> None:
        """Type ``text`` into the active comment form's editable area.

        Args:
            form: Active form locator returned by :meth:`open_task_detail`.
            text: Comment text. If empty, the method is a no-op.
        """
        if not text:
            self._log.info("[COMMENT] empty text -> skip")
            return

        comment_input = form.locator(S.SEL_COMMENT_INPUT).first
        comment_input.wait_for(state="visible", timeout=self._timeout_ms)
        comment_input.click()
        self._page.wait_for_timeout(150)
        comment_input.type(text, delay=10)
        self._page.wait_for_timeout(self._settle_ms)
        self._log.info("[COMMENT] typed: '%s'", text)

    def upload_files(
        self,
        form: Locator,
        paths: List[Path],
        *,
        max_retries: Optional[int] = None,
    ) -> None:
        """Upload files sequentially into the active form.

        Each file is uploaded one at a time using KWorks' per-click input
        pattern: clicking the upload label spawns a fresh ``input[type=file]``
        node, into which the path is set. Success is confirmed by waiting for
        the ``file_nm`` count for that filename to increase by one.

        Args:
            form: Active form locator returned by :meth:`open_task_detail`.
            paths: Ordered list of absolute file paths to upload. Single-file
                callers pass a one-element list.
            max_retries: Override the per-file retry budget for this call.
                Defaults to the value passed to the constructor.

        Raises:
            RuntimeError: If a file fails to upload after all retries.
        """
        if not paths:
            self._log.info("[UPLOAD] no files -> skip")
            return

        upload_label = form.locator(S.SEL_UPLOAD_LABEL_IN_FORM).first
        upload_label.wait_for(state="visible", timeout=self._timeout_ms)

        retries = self._default_max_retries if max_retries is None else max_retries
        total = len(paths)
        for idx, path in enumerate(paths, start=1):
            self._log.info("[STEP] upload %d/%d: %s", idx, total, path.name)
            self._upload_one_with_retry(form, upload_label, path, retries)

    def submit(self, form: Locator) -> None:
        """Press Enter inside the comment area to finalize the post.

        KWorks does not auto-submit on file attach — an explicit Enter on the
        contenteditable area registers the comment + attachments together.

        Args:
            form: Active form locator returned by :meth:`open_task_detail`.
        """
        comment_input = form.locator(S.SEL_COMMENT_INPUT).first
        comment_input.click()
        self._page.wait_for_timeout(120)
        comment_input.press("Enter")
        self._log.info("[OK] submitted (Enter pressed)")
        self._page.wait_for_timeout(max(800, self._settle_ms))

    # --------------------------------------------------------------- private

    def _is_login_page(self) -> bool:
        try:
            return (
                self._page.locator(S.SEL_LOGIN_FORM).count() > 0
                and self._page.locator(S.SEL_USER_ID).count() > 0
                and self._page.locator(S.SEL_PASSWORD).count() > 0
            )
        except Exception:
            return False

    def _close_banners(self) -> None:
        self._close_top_banner_until_gone()
        self._close_notice_banner_if_present()

    def _close_top_banner_until_gone(self) -> None:
        if self._page.locator(S.SEL_TOP_BANNER_ROOT).count() == 0:
            return

        for _ in range(1, self._banner_max_rounds + 1):
            banner = self._page.locator(S.SEL_TOP_BANNER_VISIBLE).first
            if banner.count() == 0:
                return

            banner_type = banner.get_attribute("banner") or "(unknown)"

            if banner_type == "alarm-step-2":
                target = banner.locator(S.SEL_TOP_BANNER_ALARM_NO).first
                if target.count() == 0:
                    target = banner.locator(S.SEL_TOP_BANNER_CLOSE_GENERIC).first
            elif banner_type == "ie-banner":
                target = banner.locator(S.SEL_TOP_BANNER_IE_CLOSE).first
                if target.count() == 0:
                    target = banner.locator(S.SEL_TOP_BANNER_CLOSE_GENERIC).first
            else:
                target = banner.locator(S.SEL_TOP_BANNER_CLOSE_GENERIC).first

            if target.count() == 0:
                return

            try:
                target.click(force=True, timeout=1500)
                self._page.wait_for_timeout(self._settle_ms)
            except Exception:
                self._page.wait_for_timeout(200)

    def _close_notice_banner_if_present(self) -> None:
        try:
            close_btns = self._page.locator(S.SEL_NOTICE_BANNER_CLOSE)
            cnt = close_btns.count()
            if cnt <= 0:
                return
            for i in range(cnt):
                btn = close_btns.nth(i)
                try:
                    if btn.is_visible(timeout=150):
                        btn.click(force=True, timeout=1500)
                        self._page.wait_for_timeout(self._settle_ms)
                        return
                except Exception:
                    continue
        except Exception:
            return

    def _is_filter_selected(self, scope: Locator, selector: str) -> bool:
        node = scope.locator(selector).first
        if node.count() == 0:
            return False
        try:
            classes = (node.get_attribute("class") or "").split()
            if "on" not in classes:
                return False
            icon = node.locator("i").first
            if icon.count() == 0:
                return False
            icon_classes = (icon.get_attribute("class") or "").split()
            return "all-checked" in icon_classes
        except Exception:
            return False

    def _ensure_filter(self, scope: Locator, selector: str, label: str) -> None:
        node = scope.locator(selector).first
        if node.count() == 0:
            raise RuntimeError(
                f"필터 항목을 찾지 못했습니다: {label} | selector={selector}"
            )

        if self._is_filter_selected(scope, selector):
            self._log.info("[FILTER][OK] already selected: %s", label)
            return

        self._log.info("[FILTER][FIX] selecting: %s", label)

        try:
            node.scroll_into_view_if_needed(timeout=1500)
        except Exception:
            pass

        try:
            node.click(timeout=3000)
        except Exception:
            try:
                node.click(force=True, timeout=3000)
            except Exception:
                inner_p = node.locator("p").first
                if inner_p.count() > 0:
                    inner_p.click(force=True, timeout=3000)
                else:
                    raise

        self._page.wait_for_timeout(self._settle_ms)

        if not self._is_filter_selected(scope, selector):
            try:
                self._page.evaluate(
                    "(sel) => { const el = document.querySelector(sel); if (el) el.click(); }",
                    selector,
                )
                self._page.wait_for_timeout(self._settle_ms)
            except Exception:
                pass

        if not self._is_filter_selected(scope, selector):
            raise RuntimeError(
                f"필터 자동 선택 실패: {label} | selector={selector}"
            )

        self._log.info("[FILTER][OK] selected: %s", label)

    def _ensure_all_filters(self) -> None:
        self._log.info("[STEP] Ensure left filters in '전체 업무'")

        filter_root = self._page.locator(S.SEL_ALL_TASK_FILTER).first
        filter_root.wait_for(state="visible", timeout=self._timeout_ms)
        self._page.wait_for_timeout(self._settle_ms)

        for selector, label in S.ALL_TASK_FILTERS:
            self._ensure_filter(filter_root, selector, label)

        self._log.info("[OK] '전체 업무' 필터 상태 확인/보정 완료")

    def _expand_toggles(self) -> None:
        toggles = self._page.locator(S.SEL_SECTION_TOGGLE)
        cnt = toggles.count()
        if cnt == 0:
            return

        for _round_idx in range(2):
            expanded_any = False
            for i in range(cnt):
                t = toggles.nth(i)
                try:
                    if not t.is_visible(timeout=150):
                        continue
                    is_active = t.evaluate("(el) => el.classList.contains('active')")
                    if not is_active:
                        t.click()
                        expanded_any = True
                        self._page.wait_for_timeout(250)
                except Exception:
                    continue

            if expanded_any:
                self._page.wait_for_timeout(self._settle_ms)
            else:
                break

    def _count_file_nm(self, form: Locator, file_name: str) -> int:
        sel = S.sel_uploaded_file_item(file_name)
        try:
            c_form = form.locator(sel).count()
        except Exception:
            c_form = 0
        try:
            c_all = self._page.locator(sel).count()
        except Exception:
            c_all = 0
        return max(c_form, c_all)

    def _wait_count_increase(
        self,
        form: Locator,
        file_name: str,
        before_count: int,
        timeout_ms: int,
    ) -> None:
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        last = before_count
        while time.monotonic() < deadline:
            last = self._count_file_nm(form, file_name)
            if last >= before_count + 1:
                return
            self._page.wait_for_timeout(200)
        raise RuntimeError(
            f"file_nm count did not increase within {timeout_ms}ms: "
            f"file='{file_name}', before={before_count}, last={last}"
        )

    def _get_new_file_input(self, upload_label: Locator) -> Locator:
        before = self._page.locator(S.SEL_ANY_FILE_INPUT).count()
        self._close_banners()
        upload_label.click(force=True)
        self._page.wait_for_function(
            f"() => document.querySelectorAll(\"input[type=file]\").length > {before}",
            timeout=self._new_input_timeout_ms,
        )
        return self._page.locator(S.SEL_ANY_FILE_INPUT).last

    def _upload_one_with_retry(
        self,
        form: Locator,
        upload_label: Locator,
        path: Path,
        max_retries: int,
    ) -> None:
        file_name = path.name
        file_path = str(path)
        last_err: Optional[BaseException] = None

        for attempt in range(1, max_retries + 1):
            try:
                self._close_banners()
                before = self._count_file_nm(form, file_name)
                self._log.info(
                    "[EACH] %s attempt %d/%d (before=%d)",
                    file_name,
                    attempt,
                    max_retries,
                    before,
                )

                file_input = self._get_new_file_input(upload_label)
                file_input.set_input_files([file_path])

                try:
                    file_input.dispatch_event("input")
                except Exception:
                    pass
                try:
                    file_input.dispatch_event("change")
                except Exception:
                    pass

                self._wait_count_increase(
                    form, file_name, before, self._per_file_upload_timeout_ms
                )
                after = self._count_file_nm(form, file_name)
                self._log.info(
                    "[EACH][OK] %s uploaded (count %d -> %d)",
                    file_name,
                    before,
                    after,
                )
                return

            except Exception as e:  # noqa: BLE001 - retry loop
                last_err = e
                self._log.warning(
                    "[EACH][WARN] %s attempt %d failed: %r", file_name, attempt, e
                )
                sleep_ms = self._retry_sleep_ms + (attempt - 1) * self._retry_backoff_ms
                self._page.wait_for_timeout(sleep_ms)

        raise RuntimeError(
            f"upload failed after retries: file='{file_name}', last_err={last_err!r}"
        )
