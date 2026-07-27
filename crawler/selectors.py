"""
集中管理的选择器 — 页面结构变化时只需修改此文件。
基于 Day 1 探测确认的真实 DOM 结构。
"""

SELECTORS = {
    "home": {
        "community_button": "text=社区",
    },
    "community": {
        "search_input": "input.el-input__inner",
        "search_icon": "[class*=search], .el-icon-search, button:has-text('\u641c\u7d22')",
    },
    "search_result": {
        "post_card": "a.hb-cpt__bbs-content[href*='/app/bbs/link/']",
        "game_card": "a[href*='/app/topic/game/pc/']",
    },
    "post_card": {
        "title": ".bbs-content__title",
        "author": ".list-content__username",
        "level": ".hb-level-tag__inner__text",
        "content": ".bbs-content__content",
        "publish_time": ".bbs-new-style-bottom__rich .bbs-new-style-bottom__rich-node",
        "comment_count": ".bbs-new-style-bottom__comment span:last-child",
        "like_count": ".bbs-new-style-bottom__like span:last-child",
    },
    "comment": {
        "item": ".link-comment__comment-item",
        "comment_id": "data-comment-id",
        "username": ".info-box__username",
        "time": ".info-box__create-time",
        "content": ".comment-item__content",
        "like_count": ".like-box__cnt",
        "children_container": ".comment-children",
        "child_item": "[class*='children-item']",
        "child_username": ".info-box__username",
        "child_time": ".children-item__create-time",
        "child_content": ".children-item__comment-content",
        "child_like": ".like-box__cnt",
        "expand_btn": ".comment-children button",
    },
}
