"""KWorks DOM selectors.

All KWorks selector strings live here as ``SEL_``-prefixed module constants
per convention v1. The strings are copied verbatim from the production
``worker_upload.py`` / ``ac_upload_kworks.py`` scripts — do NOT alter them
without verifying against the live DOM.
"""

from __future__ import annotations

from typing import List, Tuple


# --- Login ---
SEL_LOGIN_FORM = "#loginForm"
SEL_USER_ID = "input#userId"
SEL_PASSWORD = "input#password"
SEL_LOGIN_BUTTON = "a#normalLoginButton"

# --- Top-nav: "전체 업무" entry ---
SEL_TASK_NAV = "li[data-code='task'] a"

# --- "전체 업무" view containers ---
SEL_ALL_COLLECT_VIEW = "#allCollectView"
SEL_ALL_TASK_FILTER = "#allTaskFilter"
SEL_TASK_TITLE_NM = "#allCollectView span.js-task-title-nm"

# --- Section toggles inside the "전체 업무" view ---
SEL_SECTION_TOGGLE = "button.js-section-toggle"

# --- Top banner / notice banner (best-effort closers) ---
SEL_TOP_BANNER_ROOT = "#topBanner"
SEL_TOP_BANNER_VISIBLE = "#topBanner .top-banner-1:visible"
SEL_TOP_BANNER_CLOSE_GENERIC = "a.top-banner-close-button"
SEL_TOP_BANNER_ALARM_NO = "button.js-alarm-no"
SEL_TOP_BANNER_IE_CLOSE = "a.js-ie-banner-close"
SEL_NOTICE_BANNER_CLOSE = "section.banner-notice-wrap button.js-close"

# --- Active comment form on the task detail view ---
SEL_ACTIVE_FORM = "form.js-remark-form.comment-container.on"
SEL_COMMENT_INPUT = ".js-remark-area.js-paste-layer.comment-input[contenteditable='true']"
SEL_UPLOAD_LABEL_IN_FORM = "label.js-remark-upload-button.comment-upload-button"

# --- File-input detection / upload-result count probe ---
SEL_ANY_FILE_INPUT = "input[type='file']"


def sel_uploaded_file_item(file_name: str) -> str:
    """Build the selector that locates an uploaded image item by ``file_nm``.

    The KWorks UI mounts each uploaded image as a node with a ``file_nm``
    attribute equal to the original filename. Counting these nodes is how
    upload success is detected.

    Args:
        file_name: Filename to match exactly against the ``file_nm`` attribute.

    Returns:
        A CSS selector string. Caller is responsible for ensuring ``file_name``
        does not contain single quotes (KWorks filenames in practice do not).
    """
    return (
        f".js-post-img.document-item.image-item"
        f"[data-code='IMAGE'][file_nm='{file_name}']"
    )


def sel_task_title_by_mouseover(target_title: str) -> str:
    """Build the selector that matches a task row by its mouseover title.

    Args:
        target_title: Exact task title string to match.

    Returns:
        A CSS selector locating the corresponding ``span.js-task-title-nm``.
    """
    return f"span.js-task-title-nm[mouseover-text='{target_title}']"


# --- "전체 업무" left filters that must be fully selected before search ---
# (selector, human-readable label). Order mirrors the production scripts.
ALL_TASK_FILTERS: List[Tuple[str, str]] = [
    ("#taskGroupFilter .js-filter-button[filter-gb='3']", "업무 구분 - 전체"),
    ("#taskStatusFilter .js-filter-button[status-filter='0']", "상태 - 요청"),
    ("#taskStatusFilter .js-filter-button[status-filter='1']", "상태 - 진행"),
    ("#taskStatusFilter .js-filter-button[status-filter='4']", "상태 - 피드백"),
    ("#taskStatusFilter .js-filter-button[status-filter='2']", "상태 - 완료"),
    ("#taskPriorityFilter .js-filter-button[priority-filter='3']", "우선순위 - 긴급"),
    ("#taskPriorityFilter .js-filter-button[priority-filter='2']", "우선순위 - 높음"),
    ("#taskPriorityFilter .js-filter-button[priority-filter='1']", "우선순위 - 보통"),
    ("#taskPriorityFilter .js-filter-button[priority-filter='0']", "우선순위 - 낮음"),
    ("#taskPriorityFilter .js-filter-button[priority-filter='4']", "우선순위 - 없음"),
    ("#taskStartDateFilter .js-filter-button[start-gb-filter='0']", "시작일 - 전체"),
    ("#taskEndDateFilter .js-filter-button[end-gb-filter='0']", "마감일 - 전체"),
]
