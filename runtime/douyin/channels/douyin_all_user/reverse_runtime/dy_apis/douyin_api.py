import json
import os
import random
import re
import sys
import time
import urllib
import uuid
from typing import Optional
from pathlib import Path

import requests
requests.packages.urllib3.disable_warnings()
from bs4 import BeautifulSoup
from google.protobuf.json_format import MessageToDict

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parent.parent))

import static.Response_pb2 as ResponseProto
from builder.header import HeaderBuilder, HeaderType
from builder.params import Params
from builder.proto import ProtoBuilder
from utils.dy_util import splice_url, generate_a_bogus, generate_msToken, trans_cookies


class DouyinAPIError(Exception):
    def __init__(self, code, message, raw=None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.raw = raw


def _api_verify_tls():
    value = os.getenv("DOUYIN_API_VERIFY_TLS", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _handle_json_response(resp):
    if resp.status_code != 200:
        raise DouyinAPIError(resp.status_code, "HTTP error", raw=resp.text)
    try:
        data = resp.json()
    except ValueError as exc:
        raise DouyinAPIError("invalid_json", str(exc), raw=resp.text) from exc
    status_code = data.get("status_code") if isinstance(data, dict) else None
    if status_code not in (None, 0, "0"):
        message = data.get("status_msg") or data.get("message") or "Douyin API error"
        raise DouyinAPIError(status_code, message, raw=data)
    return data


def _request_json(method, url, **kwargs):
    kwargs.setdefault("verify", _api_verify_tls())
    kwargs.setdefault("timeout", 20)
    return _handle_json_response(requests.request(method, url, **kwargs))


def _request_response(method, url, **kwargs):
    kwargs.setdefault("verify", _api_verify_tls())
    kwargs.setdefault("timeout", 20)
    resp = requests.request(method, url, **kwargs)
    if resp.status_code != 200:
        raise DouyinAPIError(resp.status_code, "HTTP error", raw=resp.text)
    return resp



def protobuf_to_dict(message):
    return MessageToDict(message, preserving_proto_field_name=True)


def _safe_int(value, default=0):
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def _extract_im_message_text(raw_content):
    if isinstance(raw_content, dict):
        payload = raw_content
    else:
        text = str(raw_content or "").strip()
        if not text:
            return ""
        if not text.startswith("{"):
            return text
        try:
            payload = json.loads(text)
        except Exception:
            return text
    text = str(payload.get("text") or payload.get("msgHint") or "").strip()
    if text:
        return text
    return str(raw_content or "").strip()


def _messages_from_conversation_response(resp_json):
    body = (resp_json.get("body") or {}).get("messages_in_conversation_body") or {}
    messages = body.get("messages") or body.get("message_list") or []
    return messages if isinstance(messages, list) else []


def _find_confirmed_outbound_message(
    messages,
    *,
    self_uid,
    expected_text,
    min_index,
    min_create_time_ms,
):
    latest = None
    latest_index = -1
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        sender = str(msg.get("sender") or "").strip()
        if self_uid and sender != self_uid:
            continue
        text = _extract_im_message_text(msg.get("content"))
        if expected_text and text != expected_text:
            continue
        msg_index = _safe_int(msg.get("index_in_conversation"), -1)
        create_time_ms = _safe_int(msg.get("create_time"), 0)
        if min_index > 0:
            if msg_index <= min_index:
                continue
        elif min_create_time_ms > 0 and create_time_ms < min_create_time_ms:
            continue
        if msg_index > latest_index:
            latest = msg
            latest_index = msg_index
    return latest


def _summarize_im_message_for_confirm(msg):
    if not isinstance(msg, dict):
        return ""
    msg_index = _safe_int(msg.get("index_in_conversation"), -1)
    text = _extract_im_message_text(msg.get("content"))
    parts = []
    if msg_index >= 0:
        parts.append(f"index={msg_index}")
    if text:
        preview = text[:30]
        if len(text) > 30:
            preview += "..."
        parts.append(f"text={preview}")
    return ", ".join(parts)


def _im_query_params(auth):
    fp = auth.cookie.get("s_v_web_id", "")
    params = {
        "verifyFp": fp,
        "fp": fp,
        "msToken": getattr(auth, "msToken", "") or generate_msToken(),
    }
    query = splice_url(params)
    params["a_bogus"] = generate_a_bogus(query)
    return params


def _request_im_proto(url, request_proto, *, auth, referer="https://www.douyin.com/chat?isPopup=1"):
    headers = HeaderBuilder().build(HeaderType.PROTOBUF)
    headers.set_header("origin", "https://www.douyin.com")
    headers.set_header("referer", referer)
    resp = _request_response(
        "POST",
        url,
        params=_im_query_params(auth),
        headers=headers.get(),
        cookies=auth.cookie,
        data=request_proto.SerializeToString(),
    )
    response_proto = ResponseProto.Response()
    response_proto.ParseFromString(resp.content)
    return protobuf_to_dict(response_proto)


def _conversation_info_body_keys():
    return (
        "create_conversation_v2_body",
        "get_conversation_info_list_v2_response_body",
    )


def _parse_conversation_info_tuple(conv: dict) -> Optional[tuple]:
    if not isinstance(conv, dict):
        return None
    conversation_id = str(conv.get("conversation_id") or "").strip()
    conversation_short_id = _safe_int(conv.get("conversation_short_id"), 0)
    ticket = str(conv.get("ticket") or "").strip()
    if conversation_id and conversation_short_id > 0 and ticket:
        return conversation_id, conversation_short_id, ticket
    return None


def _parse_conversation_info_from_response(resp_json: dict) -> tuple:
    if not isinstance(resp_json, dict):
        raise DouyinAPIError("invalid_response", "empty IM proto response")
    error_desc = str(resp_json.get("error_desc") or "").strip()
    message = str(resp_json.get("message") or "").strip()
    body = resp_json.get("body") or {}
    for key in _conversation_info_body_keys():
        section = body.get(key) or {}
        items = section.get("conversation_info_list") or []
        if not items:
            continue
        parsed = _parse_conversation_info_tuple(items[0])
        if parsed is not None:
            return parsed
    detail = error_desc or message or "IM 接口未返回会话信息"
    raise DouyinAPIError("conversation_resolve_failed", detail, raw=resp_json)


class DouyinAPI:
    douyin_url = 'https://www.douyin.com'
    live_url = 'https://live.douyin.com'
    creator = "https://creator.douyin.com"


    @staticmethod
    def get_user_all_work_info(auth, user_url: str, **kwargs) -> list:
        """
        获取用户全部作品信息.
        :param auth: DouyinAuth object.
        :param user_url: 用户主页URL.
        :return: 全部作品信息.
        """
        max_cursor = "0"
        work_list = []
        while True:
            res_json = DouyinAPI.get_user_work_info(auth, user_url, max_cursor)
            if "aweme_list" not in res_json.keys():
                break
            works = res_json["aweme_list"]
            max_cursor = str(res_json["max_cursor"])
            work_list.extend(works)
            if res_json["has_more"] != 1:
                break
        return work_list


    @staticmethod
    def get_user_work_info(auth, user_url: str, max_cursor, **kwargs) -> dict:
        """
        获取用户作品信息.
        :param auth: DouyinAuth object.
        :param user_url:  用户主页URL.
        :param max_cursor:  上一次请求的max_cursor.
        :return:
        """
        api = "/aweme/v1/web/aweme/post/"
        user_id = user_url.split("/")[-1].split("?")[0]
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer(user_url)
        params = (
            Params()
            .with_web_defaults(
                auth,
                user_url,
                {
                    "sec_user_id": user_id,
                    "max_cursor": max_cursor,
                    "locate_query": "false",
                    "show_live_replay_strategy": "1",
                    "need_time_list": "1" if max_cursor == "0" else "0",
                    "time_list_query": "0",
                    "whale_cut_token": "",
                    "cut_version": "1",
                    "count": "18",
                    "publish_video_strategy_type": "2",
                    "version_code": "290100",
                    "version_name": "29.1.0",
                    "round_trip_time": "100",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "GET",
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )

    @staticmethod
    def get_work_info(auth, url: str) -> dict:
        """
        获取作品信息.
        :param auth: DouyinAuth object.
        :param url: 作品URL.
        :return: JSON.
        """
        api = "/aweme/v1/web/aweme/detail/"
        if "video" in url:
            aweme_id = url.split("/")[-1].split("?")[0]
        else:
            aweme_id = re.findall(r"modal_id=(\d+)", url)[0]
            url = f"https://www.douyin.com/video/{aweme_id}"
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer(url)
        params = (
            Params()
            .with_web_defaults(
                auth,
                url,
                {
                    "aweme_id": aweme_id,
                    "version_code": "190500",
                    "version_name": "19.5.0",
                    "downlink": "4.75",
                    "round_trip_time": "150",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "GET",
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )

    @staticmethod
    def get_work_out_comment(auth, url: str, cursor: str = '0', **kwargs) -> dict:
        """
        获取作品的全部一级评论.
        :param auth: DouyinAuth object.
        :param url: 作品URL.
        :param cursor: 评论游标.
        :return: JSON.
        """
        api = "/aweme/v1/web/comment/list/"
        if "video" in url:
            aweme_id = url.split("/")[-1].split("?")[0]
        else:
            aweme_id = re.findall(r"modal_id=(\d+)", url)[0]
            url = f"https://www.douyin.com/video/{aweme_id}"
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer(url)
        params = (
            Params()
            .with_web_defaults(
                auth,
                url,
                {
                    "aweme_id": aweme_id,
                    "cursor": cursor,
                    "count": "5",
                    "item_type": "0",
                    "whale_cut_token": "",
                    "cut_version": "1",
                    "rcFT": "",
                    "round_trip_time": "0",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "GET",
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )

    @staticmethod
    def get_work_all_out_comment(auth, url: str, **kwargs) -> list:
        """
        获取作品全部一级评论.
        :param auth: DouyinAuth object.
        :param url: 作品URL.
        :return:
        """
        cursor = "0"
        comment_list = []
        while True:
            res_json = DouyinAPI.get_work_out_comment(auth, url, cursor)
            comments = res_json["comments"]
            cursor = str(res_json["cursor"])
            if comments is None or len(comments) == 0:
                break
            comment_list.extend(comments)
            if res_json["has_more"] != 1:
                break
        return comment_list

    @staticmethod
    def get_work_inner_comment(auth, comment: dict, cursor: str, count: str = '3', **kwargs):
        """
        获取作品评论的二级评论.
        :param count: 要获取的二级评论数量.
        :param auth: DouyinAuth object.
        :param comment: 一级评论信息.
        :param cursor: 评论游标.
        :return:
        """
        api = "/aweme/v1/web/comment/list/reply/"
        aweme_id = comment["aweme_id"]
        comment_id = comment["cid"]
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f"https://www.douyin.com/video/{aweme_id}"
        headers.set_referer(refer)
        params = (
            Params()
            .with_web_defaults(
                auth,
                refer,
                {
                    "item_id": aweme_id,
                    "comment_id": comment_id,
                    "cut_version": "1",
                    "cursor": cursor,
                    "count": count,
                    "item_type": "0",
                    "round_trip_time": "0",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "GET",
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )

    @staticmethod
    def get_work_all_inner_comment(auth, comment: dict, **kwargs) -> list:
        """
        获取作品评论的全部二级评论.
        :param auth: DouyinAuth object.
        :param comment: 一级评论信息.
        :return: 二级评论列表.
        """
        cursor = "0"
        count = '5'
        comment_list = []
        while True:
            res_json = DouyinAPI.get_work_inner_comment(auth, comment, cursor, count)
            comments = res_json["comments"]
            cursor = str(res_json["cursor"])
            if type(comments) is list and len(comments) > 0:
                comment_list.extend(comments)
            if res_json["has_more"] != 1:
                break
        return comment_list

    @staticmethod
    def get_work_all_comment(auth, url: str, **kwargs):
        """
        获取作品全部评论.
        :param auth: DouyinAuth object.
        :param url: 作品URL.
        :return: 全部评论列表.
        """
        out_comment_list = DouyinAPI.get_work_all_out_comment(auth, url)
        for comment in out_comment_list:
            comment['reply_comment'] = []
            if comment['reply_comment_total'] > 0:
                inner_comment_list = DouyinAPI.get_work_all_inner_comment(auth, comment)
                comment['reply_comment'] = inner_comment_list
        return out_comment_list

    @staticmethod
    def get_user_info(auth, user_url: str, **kwargs) -> dict:
        """
        获取用户信息.
        :param auth: DouyinAuth object.
        :param user_url: 用户主页URL.
        :return: 用户信息.
        """
        api = f"/aweme/v1/web/user/profile/other/"
        user_id = user_url.split("/")[-1].split("?")[0]
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer(user_url)
        params = (
            Params()
            .with_web_defaults(
                auth,
                user_url,
                {
                    "publish_video_strategy_type": "2",
                    "source": "channel_pc_web",
                    "sec_user_id": user_id,
                    "personal_center_strategy": "1",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "GET",
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )

    @staticmethod
    def get_user_info_by_uid(auth, user_id: str, **kwargs) -> dict:
        """
        通过数字 uid 获取用户资料。私信 WebSocket 只给 sender uid，没有 sec_user_id。
        """
        api = f"/aweme/v1/web/user/profile/other/"
        uid = str(user_id or "").strip()
        refer = "https://www.douyin.com/"
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer(refer)
        params = (
            Params()
            .with_web_defaults(
                auth,
                refer,
                {
                    "publish_video_strategy_type": "2",
                    "source": "channel_pc_web",
                    "user_id": uid,
                    "personal_center_strategy": "1",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "GET",
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )

    @staticmethod
    def search_general_work(auth, query: str, sort_type: str = '0', publish_time: str = '0', offset: str = '0',
                            filter_duration="", search_range="", content_type="", **kwargs):
        """
        搜索综合频道作品.
        :param auth: DouyinAuth object.
        :param query: 搜索关键字.
        :param sort_type: 排序方式 0 综合排序, 1 最多点赞, 2 最新发布.
        :param publish_time: 发布时间 0 不限, 1 一天内, 7 一周内, 180 半年内.
        :param offset: 搜索结果偏移量.
        :param filter_duration: 视频时长 空字符串 不限, 0-1 一分钟内, 1-5 1-5分钟内, 5-10000 5分钟以上
        :param search_range: 搜索范围 0 不限, 1 最近看过, 2 还未看过, 3 关注的人
        :param content_type: 内容形式 0 不限, 1 视频, 2 图文
        :return: JSON数据.
        """
        api = "/aweme/v1/web/general/search/single/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f"https://www.douyin.com/search/{urllib.parse.quote(query)}?aid={uuid.uuid4()}&type=general"
        headers.set_referer(refer)
        filter_selected = json.dumps({
            "sort_type": sort_type,
            "publish_time": publish_time,
            "filter_duration": filter_duration,
            "search_range": search_range,
            "content_type": content_type,
        })
        params = (
            Params()
            .with_web_defaults(
                auth,
                refer,
                {
                    "search_channel": "aweme_general",
                    "enable_history": "1",
                    "filter_selected": filter_selected,
                    "keyword": query,
                    "search_source": "tab_search",
                    "query_correct_type": "1",
                    "is_filter_search": "1",
                    "from_group_id": "",
                    "offset": offset,
                    "count": "25",
                    "need_filter_settings": "1" if offset == "0" else "0",
                    "list_type": "single",
                    "version_code": "190600",
                    "version_name": "19.6.0",
                    "round_trip_time": "50",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "GET",
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )

    @staticmethod
    def search_some_general_work(auth, query: str, num: int, sort_type: str, publish_time: str, filter_duration="", search_range="", content_type="", **kwargs) -> list:
        """
        搜索指定数量综合频道作品.
        :param auth: DouyinAuth object.
        :param query: 搜索关键字.
        :param num: 搜索结果数量.
        :param sort_type: 排序方式 0 综合排序, 1 最多点赞, 2 最新发布.
        :param publish_time: 发布时间 0 不限, 1 一天内, 7 一周内, 180 半年内.
        :param filter_duration: 视频时长 空字符串 不限, 0-1 一分钟内, 1-5 1-5分钟内, 5-10000 5分钟以上
        :param search_range: 搜索范围 0 不限, 1 最近看过, 2 还未看过, 3 关注的人
        :param content_type: 内容形式 0 不限, 1 视频, 2 图文
        :return: 作品列表.
        """
        offset = "0"
        work_list = []
        while True:
            res_json = DouyinAPI.search_general_work(auth, query, sort_type, publish_time, offset,
                                                     filter_duration, search_range, content_type)
            works = res_json["data"]
            work_list.extend(works)
            if res_json["has_more"] != 1 or len(work_list) >= num:
                break
            offset = str(int(offset) + len(works))
        if len(work_list) > num:
            work_list = work_list[:num]
        return work_list

    @staticmethod
    def search_some_user(auth, query: str, num: int, **kwargs) -> list:
        """
        搜索指定数量用户.
        :param auth: DouyinAuth object.
        :param query: 搜索关键字.
        :param num: 搜索结果数量.
        :return: 用户列表.
        """
        offset = "0"
        count = "25"
        user_list = []
        while True:
            res_json = DouyinAPI.search_user(auth, query, offset, count)
            users = res_json["user_list"]
            user_list.extend(users)
            if res_json["has_more"] != 1 or len(user_list) >= num:
                break
            offset = str(int(offset) + int(count))
        if len(user_list) > num:
            user_list = user_list[:num]
        return user_list


    @staticmethod
    def search_user(auth, query: str, offset: str = '0', num: str = '25', douyin_user_fans="", douyin_user_type="", **kwargs):
        """
        搜索用户.
        :param auth: DouyinAuth object.
        :param query:  搜索关键字.
        :param offset:  搜索结果偏移量.
        :param num:  搜索结果数量.
        :param douyin_user_fans: 粉丝数量 空字符串 (0_1k 1000以下) (1k_1w 1000-10000) (1w_10w 10000-100000) (10w_100w 10w-100w粉丝) (100w_ 100w以上)
        :param douyin_user_type: 用户类型 空字符串 不限 common_user 普通用户 enterprise_user 企业用户 personal_user 个人认证用户
        :return: JSON数据.
        """
        api = "/aweme/v1/web/discover/search"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f'https://www.douyin.com/search/{urllib.parse.quote(query)}?aid={uuid.uuid4()}&type=general'
        headers.set_referer(refer)
        params = (
            Params()
            .with_web_defaults(
                auth,
                refer,
                {
                    "search_channel": "aweme_user_web",
                    "search_filter_value": r'{"douyin_user_fans":["%s"],"douyin_user_type":["%s"]}' % (
                        douyin_user_fans,
                        douyin_user_type,
                    ),
                    "keyword": query,
                    "search_source": "switch_tab",
                    "query_correct_type": "1",
                    "is_filter_search": "1",
                    "offset": offset,
                    "count": num,
                    "need_filter_settings": "1" if offset == "0" else "0",
                    "list_type": "single",
                    "round_trip_time": "150",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "GET",
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )

    @staticmethod
    def search_live(auth, query: str, offset: str = '0', num: str = '25', **kwargs):
        """
        搜索直播.
        :param auth: DouyinAuth object.
        :param query:  搜索关键字.
        :param offset:  搜索结果偏移量.
        :param num:  搜索数量.
        :return: JSON数据.
        """
        api = "/aweme/v1/web/live/search/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f'https://www.douyin.com/search/{urllib.parse.quote(query)}?aid={uuid.uuid4()}&type=live'
        headers.set_referer(refer)
        params = Params()
        params = (
            Params()
            .with_web_defaults(
                auth,
                refer,
                {
                    "search_channel": "aweme_live",
                    "keyword": query,
                    "search_source": "normal_search",
                    "query_correct_type": "1",
                    "is_filter_search": "0",
                    "from_group_id": "",
                    "offset": offset,
                    "count": num,
                    "need_filter_settings": "1" if offset == "0" else "0",
                    "list_type": "single",
                    "round_trip_time": "50",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "GET",
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )

    @staticmethod
    def search_some_live(auth, query: str, num: int, **kwargs) -> list:
        """
        搜索指定数量直播.
        :param auth: DouyinAuth object.
        :param query:  搜索关键字.
        :param num:  搜索数量.
        :return: 直播列表.
        """
        offset = "0"
        count = "25"
        live_list = []
        while True:
            res_json = DouyinAPI.search_live(auth, query, offset, count)
            lives = res_json["data"]
            live_list.extend(lives)
            if res_json["has_more"] != 1 or len(live_list) >= num:
                break
            offset = str(int(offset) + int(count))
        if len(live_list) > num:
            live_list = live_list[:num]
        return live_list

    @staticmethod
    def get_user_favorite(auth, sec_id: str, max_cursor: str = '0', num: str = '18', **kwargs):
        """
        获取用户收藏.
        :param auth: DouyinAuth object.
        :param sec_id:  用户SECID.
        :param max_cursor:  翻页游标.
        :param num: 要获取的收藏数量.
        :return: JSON.
        """
        api = "/aweme/v1/web/aweme/favorite/"
        headers = HeaderBuilder.build(HeaderType.GET)
        refer = f"https://www.douyin.com/user/{sec_id}?showTab=like"
        headers.set_referer(refer)
        params = (
            Params()
            .with_web_defaults(
                auth,
                refer,
                {
                    "sec_user_id": sec_id,
                    "max_cursor": max_cursor,
                    "min_cursor": "0",
                    "whale_cut_token": "",
                    "cut_version": "1",
                    "count": num,
                    "publish_video_strategy_type": "2",
                    "round_trip_time": "100",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "GET",
            f"https://www.douyin.com{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )

    @staticmethod
    def get_my_uid(auth, **kwargs) -> int:
        """
        获取自己的用户ID.
        :param auth: DouyinAuth object.
        :return: 用户ID.
        """
        url = "https://www.douyin.com/aweme/v1/web/query/user/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = "https://www.douyin.com/"
        headers.set_header("referer", refer)
        params = (
            Params()
            .with_web_defaults(auth, refer, {"publish_video_strategy_type": "2"})
            .with_a_bogus()
        )
        resp = _request_json(
            "GET",
            url,
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )
        return int(resp["user_uid"])

    @staticmethod
    def get_my_sec_uid(auth, **kwargs) -> str:
        """
        获取自己的SECID.
        :param auth: DouyinAuth object.
        :return: SECID.
        """
        url = "https://www.douyin.com/aweme/v1/web/query/user/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = "https://www.douyin.com/"
        headers.set_header("referer", refer)
        params = (
            Params()
            .with_web_defaults(auth, refer, {"publish_video_strategy_type": "2"})
            .with_a_bogus()
        )
        resp = _request_json(
            "GET",
            url,
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )
        sec_uid = resp.get("sec_uid") or resp.get("secUid") or ""
        if not sec_uid:
            raise DouyinAPIError("missing_sec_uid", "响应中未找到 sec_uid", raw=resp)
        return sec_uid


    @staticmethod
    def get_live_info(auth_, live_id, **kwargs):
        """
        获取直播间信息.
        :param live_id: 直播间ID
        :return: 直播间ID, 用户ID, ttwid
        """
        url = "https://live.douyin.com/" + live_id
        headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "zh-CN,zh;q=0.9,zh-TW;q=0.8,en;q=0.7,ja;q=0.6",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "priority": "u=0, i",
            "referer": "https://live.douyin.com/?from_nav=1",
            "sec-ch-ua": "\"Not)A;Brand\";v=\"8\", \"Chromium\";v=\"138\", \"Google Chrome\";v=\"138\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        }
        res = _request_response("GET", url, headers=headers, cookies=auth_.cookie)
        ttwid = str(res.cookies.get_dict().get("ttwid") or "").strip()
        if not ttwid:
            raise DouyinAPIError("missing_ttwid", "响应中未找到 ttwid", raw=res.text)
        soup = BeautifulSoup(res.text, 'html.parser')
        scripts = soup.select('script[nonce]')
        for script in scripts:
            if script.string is not None and 'roomId' in script.string:
                try:
                    user_id = re.findall(r'\\"user_unique_id\\":\\"(\d+)\\"', script.string)[0]
                    room_id = re.findall(r'\\"roomId\\":\\"(\d+)\\"', script.string)[0]
                    user_unique_id = re.findall(r'\\"user_unique_id\\":\\"(\d+)\\"', script.string)[0]
                    room_info = re.findall(r'\\"roomInfo\\":\{\\"room\\":\{\\"id_str\\":\\".*?\\",\\"status\\":(.*?),\\"status_str\\":\\".*?\\",\\"title\\":\\"(.*?)\\"', script.string)[0]
                    # "anchor\":{\"id_str\":\"3998258005032616\",\
                    anchor_id = re.findall(r'\\"anchor\\":\{\\"id_str\\":\\"(\d+)\\"', script.string)[0]
                    # , \"sec_uid\":\"M
                    sec_uid = re.findall(r'\\"sec_uid\\":\\"(.*?)\\"', script.string)[0]
                    room_status = room_info[0]
                    room_title = room_info[1]
                    res = {
                        "room_id": room_id,
                        "user_id": user_id,
                        "user_unique_id": user_unique_id,
                        "author_id": anchor_id,
                        "anchor_id": anchor_id,
                        "sec_uid": sec_uid,
                        "ttwid": ttwid,
                        # 2 是直播中 4 是未开播
                        "room_status": room_status,
                        "room_title": room_title
                    }
                    return res
                except Exception:
                    pass
        raise DouyinAPIError("live_info_parse_failed", f"未能解析直播间信息: {live_id}", raw=res.text)

    @staticmethod
    def get_live_production(auth, url: str, room_id: str, author_id: str, offset: str, **kwargs):
        """
        获取直播间的商品信息.
        :param auth: DouyinAuth object.
        :param url: 直播间链接.
        :param room_id: 直播间ID
        :param author_id: 主播ID
        :param offset: 翻页游标.
        :return: JSON 商品列表.
        """
        api = f"/live/promotions/page/"
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_header("origin", DouyinAPI.live_url)
        headers.set_referer(url)
        params = (
            Params()
            .with_web_defaults(
                auth,
                url,
                {
                    "room_id": room_id,
                    "author_id": author_id,
                    "offset": offset,
                    "limit": "20",
                    "version_code": "210800",
                    "version_name": "21.8.0",
                    "screen_width": "2560",
                    "screen_height": "1440",
                    "browser_version": "121.0.0.0",
                    "engine_version": "121.0.0.0",
                    "cpu_core_num": "20",
                    "round_trip_time": "50",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "POST",
            f"{DouyinAPI.live_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )

    @staticmethod
    def get_all_live_production(auth, url: str, **kwargs):
        """
        获取直播间的所有商品信息.
        :param auth: DouyinAuth object.
        :param url: 直播间链接.
        :return:
        """
        room_info = DouyinAPI.get_live_info(auth, url.split("/")[-1].split("?")[0])
        room_id = room_info["room_id"]
        author_id = room_info.get("author_id") or room_info.get("anchor_id")
        offset = "0"
        production_list = []
        while True:
            res_json = DouyinAPI.get_live_production(auth, url, room_id, author_id, offset)
            productions = res_json["promotions"]
            production_list.extend(productions)
            offset = str(res_json["next_offset"])
            if offset == "-1":
                break
        return production_list

    @staticmethod
    def get_live_production_detail(auth, url, ec_promotion_id, sec_author_id, live_room_id, **kwargs):
        """
        获取直播间商品详情.
        :param auth: DouyinAuth object.
        :param url: 直播间链接.
        :param ec_promotion_id: 商品ID.
        :param sec_author_id: 主播ID
        :param live_room_id: 直播间ID
        :return: JSON 商品详情.
        """
        api = f"/ecom/product/detail/saas/pc/"
        headers = HeaderBuilder().build(HeaderType.FORM)
        headers.set_header("origin", DouyinAPI.live_url)
        headers.set_referer(url)
        headers.with_csrf(auth.cookie_str)
        params = (
            Params()
            .with_web_defaults(
                auth,
                url,
                {
                    "is_h5": "1",
                    "origin_type": "638301",
                    "version_code": "",
                    "version_name": "",
                    "downlink": "1.7",
                    "round_trip_time": "200",
                },
            )
        )
        data = {
            "bff_type": "2",
            "ec_promotion_id": ec_promotion_id,
            "is_h5": "1",
            "item_id": "0",
            "live_room_id": live_room_id,
            "origin_type": "638301",
            "promotion_ids": ec_promotion_id,
            "room_id": live_room_id,
            "sec_author_id": sec_author_id,
            "use_new_price": "1"
        }
        params.with_a_bogus(data)
        res = requests.post(f'{DouyinAPI.live_url}{api}', headers=headers.get(), params=params.get(),
                            cookies=auth.cookie, data=data, verify=False)
        return res.json()

    @staticmethod
    def collect_aweme(auth, aweme_id: str, action: str = '1', **kwargs):
        """
        收藏或取消收藏视频.
        :param auth: DouyinAuth object.
        :param aweme_id: 视频ID.
        :param action: 1: 收藏, 0: 取消收藏.
        :return: 响应JSON.
        """
        api = '/aweme/v1/web/aweme/collect/'
        headers = HeaderBuilder().build(HeaderType.FORM)
        refer = "https://www.douyin.com/?recommend=1"
        headers.set_referer(refer)
        headers.with_bd(api, auth)
        headers.with_csrf(auth.cookie_str)
        headers.set_header("origin", DouyinAPI.douyin_url)
        params = (
            Params()
            .with_web_defaults(
                auth,
                refer,
                {"round_trip_time": "50"},
            )
        )
        data = {
            "action": action,
            "aweme_id": aweme_id,
            "aweme_type": "0",
        }
        params.with_a_bogus(data)
        res = requests.post(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), params=params.get(),
                            cookies=auth.cookie, data=data, verify=False)
        return res.json()

    @staticmethod
    def move_collect_aweme(auth, aweme_id: str, collect_name: str, collect_id: str, **kwargs):
        """
        移动视频到指定收藏夹（需要先收藏视频）
        :param collect_name: 收藏夹名称
        :param collect_id: 收藏夹ID
        :param auth: DouyinAuth object.
        :param aweme_id: 视频ID.
        :return: 响应JSON.
        """
        api = '/aweme/v1/web/collects/video/move/'
        headers = HeaderBuilder().build(HeaderType.FORM)
        refer = "https://www.douyin.com/?recommend=1"
        headers.set_referer(refer)
        headers.with_bd(api, auth)
        headers.with_csrf(auth.cookie_str)
        headers.set_header("origin", DouyinAPI.douyin_url)
        params = (
            Params()
            .with_web_defaults(
                auth,
                refer,
                {
                    "collects_name": collect_name,
                    "item_ids": aweme_id,
                    "item_type": "2",
                    "move_collects_list": collect_id,
                    "to_collects_id": collect_id,
                    "update_collects_sort": "true",
                    "round_trip_time": "50",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "POST",
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )

    @staticmethod
    def remove_collect_aweme(auth, aweme_id: str, collect_name: str, collect_id: str, **kwargs):
        """
        从指定收藏夹中移除视频（需要先收藏视频）
        :param collect_name: 收藏夹名称
        :param collect_id: 收藏夹ID
        :param auth: DouyinAuth object.
        :param aweme_id: 视频ID.
        :return: 响应JSON.
        """
        api = '/aweme/v1/web/collects/video/move/'
        headers = HeaderBuilder().build(HeaderType.FORM)
        refer = "https://www.douyin.com/user/self?showTab=favorite_collection"
        headers.set_referer(refer)
        headers.with_bd(api, auth)
        headers.with_csrf(auth.cookie_str)
        headers.set_header("origin", DouyinAPI.douyin_url)
        params = (
            Params()
            .with_web_defaults(
                auth,
                refer,
                {
                    "collects_name": collect_name,
                    "from_collects_id": collect_id,
                    "item_ids": aweme_id,
                    "item_type": "2",
                    "round_trip_time": "50",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "POST",
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )

    @staticmethod
    def get_collect_list(auth, **kwargs):
        """
        获取我的收藏夹列表
        :param auth: DouyinAuth object.
        :return: JSON.
        """
        api = "/aweme/v1/web/collects/list/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = "https://www.douyin.com/?recommend=1"
        headers.set_referer(refer)
        params = (
            Params()
            .with_web_defaults(
                auth,
                refer,
                {
                    "cursor": "0",
                    "count": "20",
                    "downlink": "5.95",
                    "round_trip_time": "200",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "GET",
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )

    @staticmethod
    def get_user_follower_list(auth, user_id: str, sec_id: str, max_time: str = '0', count: str = '20', **kwargs):
        """
        获取用户的粉丝列表
        :param auth: DouyinAuth object.
        :param user_id: 用户ID.
        :param sec_id: 用户sec_id.
        :param max_time: 最大时间戳.
        :param count: 数量.
        :return:  JSON.
        """
        api = "/aweme/v1/web/user/follower/list/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f"https://www.douyin.com/user/{sec_id}"
        headers.set_referer(refer)
        params = (
            Params()
            .with_web_defaults(
                auth,
                refer,
                {
                    "user_id": user_id,
                    "sec_user_id": sec_id,
                    "offset": "0",
                    "min_time": "0",
                    "max_time": max_time,
                    "count": count,
                    "source_type": "2" if max_time == "0" else "1",
                    "gps_access": "0",
                    "address_book_access": "0",
                    "round_trip_time": "150",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "GET",
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )

    @staticmethod
    def get_some_user_follower_list(auth, user_id: str, sec_id: str, num: int, **kwargs) -> list:
        """
        获取用户的前num个粉丝列表
        :param auth: DouyinAuth object.
        :param user_id: 用户ID.
        :param sec_id: 用户sec_id.
        :param num: 要获取的数量
        :return: 粉丝列表.
        """
        max_time = "0"
        count = "20"
        follower_list = []
        while True:
            res_json = DouyinAPI.get_user_follower_list(auth, user_id, sec_id, max_time, count)
            followers = res_json["followers"]
            follower_list.extend(followers)
            if res_json["has_more"] != 1 or len(follower_list) >= num:
                break
            max_time = res_json["min_time"]
        if len(follower_list) > num:
            follower_list = follower_list[:num]
        return follower_list

    @staticmethod
    def get_user_following_list(auth, user_id: str, sec_id: str, max_time: str = '0', count: str = '20', **kwargs):
        """
        获取用户的关注列表
        :param auth: DouyinAuth object.
        :param user_id: 用户ID.
        :param sec_id: 用户sec_id.
        :param max_time: 最大时间戳.
        :param count: 数量.
        :return:
        """
        api = "/aweme/v1/web/user/following/list/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f"https://www.douyin.com/user/{sec_id}"
        headers.set_referer(refer)
        params = (
            Params()
            .with_web_defaults(
                auth,
                refer,
                {
                    "user_id": user_id,
                    "sec_user_id": sec_id,
                    "offset": "0",
                    "min_time": "0",
                    "max_time": max_time,
                    "count": count,
                    "source_type": "2" if max_time == "0" else "1",
                    "gps_access": "0",
                    "address_book_access": "0",
                    "is_top": "1",
                    "round_trip_time": "150",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "GET",
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )

    @staticmethod
    def get_some_user_following_list(auth, user_id: str, sec_id: str, num: int, **kwargs) -> list:
        """
        获取用户的前num个关注列表
        :param auth: DouyinAuth object.
        :param user_id: 用户ID.
        :param sec_id: 用户sec_id.
        :param num: 要获取的数量
        :return: 关注列表.
        """
        max_time = "0"
        count = "20"
        following_list = []
        while True:
            res_json = DouyinAPI.get_user_following_list(auth, user_id, sec_id, max_time, count)
            followings = res_json["followings"]
            following_list.extend(followings)
            if res_json["has_more"] != 1 or len(following_list) >= num:
                break
            max_time = res_json["min_time"]
        if len(following_list) > num:
            following_list = following_list[:num]
        return following_list

    @staticmethod
    def get_notice_list(auth, min_time='0', max_time='0', count='10', notice_group='700', **kwargs):
        """
        获得通知
        :param auth: DouyinAuth object.
        :param min_time: 最小时间戳.
        :param max_time: 最大时间戳.
        :param count: 数量.
        :param notice_group: 消息类型 700 全部消息 401 粉丝 601 @我的 2 评论 3 点赞 520 弹幕
        :return: JSON.
        """
        api = "/aweme/v1/web/notice/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = "https://www.douyin.com/?recommend=1"
        headers.set_referer(refer)
        params = (
            Params()
            .with_web_defaults(
                auth,
                refer,
                {
                    "is_new_notice": "1",
                    "is_mark_read": "1",
                    "notice_group": notice_group,
                    "count": count,
                    "min_time": min_time,
                    "max_time": max_time,
                    "round_trip_time": "50",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "GET",
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )

    @staticmethod
    def get_some_notice_list(auth, num: int = 20, notice_group='700', **kwargs) -> list:
        """
        获得前num条通知
        :param auth: DouyinAuth object.
        :param num: 数量.
        :param notice_group: 消息类型 | 700 全部消息 401 粉丝 601 @我的 2 评论 3 点赞 520 弹幕
        :return:
        """
        min_time = "0"
        max_time = "0"
        count = "10"
        notice_list = []
        while True:
            res_json = DouyinAPI.get_notice_list(auth, min_time, max_time, count, notice_group)
            notices = res_json["notice_list_v2"]
            notice_list.extend(notices)
            if res_json["has_more"] != 1 or len(notice_list) >= num:
                break
            min_time = res_json["min_time"]
            max_time = res_json["max_time"]
        if len(notice_list) > num:
            notice_list = notice_list[:num]
        return notice_list

    @staticmethod
    def get_feed(auth, count='20', refresh_index='2', **kwargs):
        """
        获取首页推荐视频
        :param auth: DouyinAuth object.
        :param count: 数量.
        :param refresh_index: 刷新索引.
        :return: JSON.
        """
        api = "/aweme/v1/web/module/feed/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = "https://www.douyin.com/"
        headers.set_referer(refer)
        params = (
            Params()
            .with_web_defaults(
                auth,
                refer,
                {
                    "module_id": "3003101",
                    "count": count,
                    "filterGids": "",
                    "presented_ids": "",
                    "refresh_index": refresh_index,
                    "refer_id": "",
                    "refer_type": "10",
                    "awemePcRecRawData": '{"is_client":false}',
                    "Seo-Flag": "0",
                    "install_time": "1715480185",
                    "round_trip_time": "100",
                },
            )
            .with_a_bogus()
        )
        return _request_json(
            "GET",
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )



    @staticmethod
    def get_rank_list(auth, room_id: str, anchor_id: str, sec_anchor_id: str):
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = "https://live.douyin.com"
        headers.set_referer(refer)
        url = "https://live.douyin.com/webcast/ranklist/audience/"
        params = Params()

        # params = {
        #     "aid": "6383",
        #     "app_name": "douyin_web",
        #     "live_id": "1",
        #     "device_platform": "web",
        #     "language": "zh-CN",
        #     "enter_from": "web_live",
        #     "cookie_enabled": "true",
        #     "screen_width": "2560",
        #     "screen_height": "1600",
        #     "browser_language": "zh-CN",
        #     "browser_platform": "Win32",
        #     "browser_name": "Chrome",
        #     "browser_version": "138.0.0.0",
        #     "webcast_sdk_version": "2450",
        #     "room_id": "7527483067720583955",
        #     "anchor_id": "3998258005032616",
        #     "sec_anchor_id": "MS4wLjABAAAA2F3NX6RiboGdfcX98Hpp3JESCY-Z8Tw8jQD8aqs25qhdnQSvMyyAbVvnLq5NT_rN",
        #     "ignoreToast": "true",
        #     "rank_type": "30",
        #     "update_scene": "rank_message",
        #     "msToken": "-HpOqCxjx1MRFQP00onCIVOe7UekYXQKcayCMuaffyovdtusmV13ZavT6mmX24sWMlGVdZza4F-MWiGt6iddfmElCqbOu59e-RiUXuBfYxqkbM-OZRHlLQn6dcDCagr8olEfvFxMvSye3lYz4-_pvuAkUQjA-a8oShkGqRiUXlrD",
        #     "a_bogus": "OXsfhHXEd2WbedKSYCY5t53lU8DlNsuyFBiQbinue5Cuch0bDmPtknebJxow1Mjo5SpziCl77EUMbxxb0VXi11HpqmkvS8JWbTICVh8LgqqRTFisEHRTewgEHJebWOJEm5ojJ1k3ItmP2EA4L1riUQAjCAaj4Qkp/rrRda4aNItggzs9FNqxuxSDOXFNBRI4YE=="
        # }
        params.add_param("aid", "6383")
        params.add_param("app_name", "douyin_web")
        params.add_param("live_id", "1")
        params.add_param("device_platform", "web")
        params.add_param("language", "zh-CN")
        params.add_param("enter_from", "web_live")
        params.add_param("cookie_enabled", "true")
        params.add_param("screen_width", "2560")
        params.add_param("screen_height", "1600")
        params.add_param("browser_language", "zh-CN")
        params.add_param("browser_platform", "Win32")
        params.add_param("browser_name", "Chrome")
        params.add_param("browser_version", "138.0.0.0")
        params.add_param("webcast_sdk_version", "2450")
        params.add_param("room_id", room_id)
        params.add_param("anchor_id", anchor_id)
        params.add_param("sec_anchor_id", sec_anchor_id)
        params.add_param("ignoreToast", "true")
        params.add_param("rank_type", "30")
        params.add_param("update_scene", "rank_message")
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        response = requests.get(url, headers=headers.get(), params=params.get(),
                           cookies=auth.cookie, verify=False)
        return response.json()

    @staticmethod
    def get_webcast_detail(auth, user_id, room_id, url: str):
        api = f"/webcast/im/fetch/"
        headers = HeaderBuilder().build(HeaderType.FORM)
        headers.set_header("origin", DouyinAPI.live_url)
        headers.set_referer(url)
        headers.with_csrf(auth.cookie_str)
        params = Params()
        params.add_param("resp_content_type", "protobuf")
        params.add_param("did_rule", "3")
        params.add_param("device_id", "")
        params.add_param("app_name", "douyin_web")
        params.add_param("endpoint", "live_pc")
        params.add_param("support_wrds", "1")
        params.add_param("user_unique_id", str(user_id))
        params.add_param("identity", "audience")
        params.add_param("need_persist_msg_count", "15")
        params.add_param("insert_task_id", "")
        params.add_param("live_reason", "")
        params.add_param("room_id", room_id)
        params.add_param("version_code", "180800")
        params.add_param("last_rtt", "0")
        params.add_param("live_id", "1")
        params.add_param("aid", "6383")
        params.add_param("fetch_rule", "1")
        params.add_param("cursor", "")
        params.add_param("internal_ext", "")
        params.add_param("device_platform", "web")
        params.add_param("cookie_enabled", "true")
        params.add_param("screen_width", "2560")
        params.add_param("screen_height", "1440")
        params.add_param("browser_language", "en")
        params.add_param("browser_platform", "Win32")
        params.add_param("browser_name", "Mozilla")
        params.add_param("browser_version",
                         "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")
        params.add_param("browser_online", "true")
        params.add_param("tz_name", "Asia/Shanghai")
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        res = requests.get(f'{DouyinAPI.live_url}{api}', headers=headers.get(), params=params.get(),
                           cookies=auth.cookie, verify=False)
        return res.content

    @staticmethod
    def diggLiveRoom(auth, room_id: str, count: str = '1'):
        api = "/webcast/room/like/"
        headers = HeaderBuilder().build(HeaderType.FORM)
        refer = f"https://live.douyin.com/{room_id}"
        headers.set_header("origin", DouyinAPI.douyin_url)
        headers.with_csrf(auth.cookie_str)
        headers.set_referer(refer)
        params = Params()
        params.add_param("aid", '6383')
        params.add_param("app_name", 'douyin_web')
        params.add_param("live_id", '1')
        params.add_param("device_platform", 'web')
        params.add_param("language", 'zh-CN')
        params.add_param("enter_from", 'web_live')
        params.add_param("cookie_enabled", 'true')
        params.add_param("screen_width", '2560')
        params.add_param("screen_height", '1440')
        params.add_param("browser_language", 'zh-CN')
        params.add_param("browser_platform", 'Win32')
        params.add_param("browser_name", 'Edge')
        params.add_param("browser_version", '130.0.0.0')
        params.add_param("room_id", room_id)
        params.add_param("count", count)
        params.add_param("msToken", auth.msToken)
        data = {
        }
        params.with_a_bogus(data)
        res = requests.post(f'{DouyinAPI.live_url}{api}', headers=headers.get(), params=params.get(),
                            cookies=auth.cookie, data=data, verify=False)
        return res.json()

    @staticmethod
    def sendMsgInRoom(auth, room_id: str, content: str = ''):
        api = "/webcast/room/chat/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f"https://live.douyin.com/{room_id}"
        headers.set_header("Origin", DouyinAPI.douyin_url)
        headers.with_bd(api, auth)
        headers.with_csrf(auth.cookie_str)
        headers.set_referer(refer)
        params = Params()
        params.add_param("aid", '6383')
        params.add_param("app_name", 'douyin_web')
        params.add_param("live_id", '1')
        params.add_param("device_platform", 'web')
        params.add_param("language", 'zh-CN')
        params.add_param("enter_from", 'web_others_homepage')
        params.add_param("cookie_enabled", 'true')
        params.add_param("screen_width", '2560')
        params.add_param("screen_height", '1440')
        params.add_param("browser_language", 'zh-CN')
        params.add_param("browser_platform", 'Win32')
        params.add_param("browser_name", 'Edge')
        params.add_param("browser_version", '130.0.0.0')
        params.add_param("room_id", room_id)
        params.add_param("content", content)
        params.add_param("type", '0')
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        res = requests.get(f'{DouyinAPI.live_url}{api}', headers=headers.get(), params=params.get(),
                           cookies=auth.cookie, verify=False)
        return res.json()

    @staticmethod
    def publish_comment(auth, aweme_id: str, content: str = '', reply_id="", **kwargs):
        """
        发布评论
        :param auth: DouyinAuth object.
        :param aweme_id: 视频ID.
        :param content: 评论内容.
        :param reply_id: 回复评论ID.
        :return: JSON.
        """
        api = "/aweme/v1/web/comment/publish"
        headers = HeaderBuilder().build(HeaderType.FORM)
        refer = f"https://www.douyin.com/discover?modal_id={aweme_id}"
        headers.set_header("Origin", DouyinAPI.douyin_url)
        headers.with_bd(api, auth)
        headers.with_csrf(auth.cookie_str)
        headers.set_referer(refer)
        params = (
            Params()
            .with_web_defaults(
                auth,
                refer,
                {
                    "app_name": "aweme",
                    "enter_from": "discover",
                    "previous_page": "discover",
                    "round_trip_time": "100",
                },
            )
        )
        data = {
            "aweme_id": aweme_id,
            "comment_send_celltime": random.randint(1000, 20000),
            "comment_video_celltime": random.randint(1000, 20000),
        }
        if reply_id != "":
            data["reply_id"] = reply_id
        data["text"] = content
        data["text_extra"] = []
        params.with_a_bogus(data)
        return _request_json(
            "POST",
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
            data=data,
        )

    @staticmethod
    def _resolve_self_uid(auth, my_id=None) -> int:
        if my_id is not None:
            try:
                resolved = int(my_id)
            except (TypeError, ValueError):
                resolved = 0
            if resolved > 0:
                return resolved
        return int(auth.get_uid())

    @staticmethod
    def find_conversation_in_lists(
        auth,
        *,
        conversation_id: str = "",
        peer_user_id: int = 0,
        my_id=None,
        max_pages: int = 5,
    ) -> Optional[tuple]:
        """从已有会话列表中查找 ticket，避免重复 create 失败。"""
        targets: set[str] = set()
        cid = str(conversation_id or "").strip()
        if cid:
            targets.add(cid)
        peer = int(peer_user_id or 0)
        self_uid = DouyinAPI._resolve_self_uid(auth, my_id)
        if peer > 0 and self_uid > 0:
            targets.add(f"0:1:{self_uid}:{peer}")
            targets.add(f"0:1:{peer}:{self_uid}")
        if not targets:
            return None

        def _match(conv: dict) -> Optional[tuple]:
            conv_id = str(conv.get("conversation_id") or "").strip()
            if conv_id not in targets:
                return None
            return _parse_conversation_info_tuple(conv)

        for conv in DouyinAPI.get_all_conversation_list(
            auth,
            limit=50,
            max_pages=max_pages,
        ):
            parsed = _match(conv)
            if parsed is not None:
                return parsed

        for conv in DouyinAPI.get_all_stranger_conversation_list(
            auth,
            count=50,
            max_pages=max_pages,
        ):
            parsed = _match(conv)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def resolve_or_create_conversation(auth, to_user_id: int, **kwargs):
        """优先复用已有会话，必要时再 create。"""
        conversation_id = str(kwargs.get("conversation_id") or "").strip()
        my_id = kwargs.get("my_id")
        found = DouyinAPI.find_conversation_in_lists(
            auth,
            conversation_id=conversation_id,
            peer_user_id=int(to_user_id),
            my_id=my_id,
        )
        if found is not None:
            return found
        return DouyinAPI.create_conversation(
            auth,
            int(to_user_id),
            my_id=my_id,
        )

    @staticmethod
    def create_conversation(auth, to_user_id: int, **kwargs):
        """
        创建私信对话.
        :param auth: DouyinAuth object.
        :param to_user_id: 私信对话接收者ID.
        :return: 私信对话ID.
        """
        my_id = DouyinAPI._resolve_self_uid(auth, kwargs.get("my_id"))
        url = "https://imapi.douyin.com/v2/conversation/create"
        request_proto = ProtoBuilder.build_create_conversation_request(
            auth,
            int(to_user_id),
            my_id,
        )
        resp_json = _request_im_proto(
            url,
            request_proto,
            auth=auth,
            referer="https://www.douyin.com/",
        )
        return _parse_conversation_info_from_response(resp_json)

    @staticmethod
    def get_conversation_list(
        auth,
        *,
        cursor: int = 0,
        limit: int = 20,
        conversation_type: int = 1,
        sort_type: int = 1,
        with_cold: bool = False,
        include_role=None,
        exclude_role=None,
        push_status=None,
        **kwargs,
    ) -> dict:
        """获取普通会话列表（v1/conversation/list）。"""
        url = "https://imapi.douyin.com/v1/conversation/list"
        request_proto = ProtoBuilder.build_get_user_conversation_list_request(
            auth,
            cursor=cursor,
            limit=limit,
            conversation_type=conversation_type,
            sort_type=sort_type,
            with_cold=with_cold,
            include_role=include_role,
            exclude_role=exclude_role,
            push_status=push_status,
        )
        return _request_im_proto(url, request_proto, auth=auth)

    @staticmethod
    def get_all_conversation_list(
        auth,
        *,
        limit: int = 50,
        conversation_type: int = 1,
        sort_type: int = 1,
        with_cold: bool = False,
        max_pages: int = 20,
        **kwargs,
    ) -> list:
        conversations = []
        cursor = 0
        for _ in range(max_pages):
            resp = DouyinAPI.get_conversation_list(
                auth,
                cursor=cursor,
                limit=limit,
                conversation_type=conversation_type,
                sort_type=sort_type,
                with_cold=with_cold,
            )
            body = (resp.get("body") or {}).get("get_conversation_list_body") or {}
            chunk = body.get("list") or []
            if not chunk:
                break
            conversations.extend(chunk)
            if not body.get("has_more"):
                break
            next_cursor = body.get("next_cursor", 0)
            try:
                next_cursor = int(next_cursor)
            except (TypeError, ValueError):
                next_cursor = 0
            if next_cursor <= 0 or next_cursor == cursor:
                break
            cursor = next_cursor
        return conversations

    @staticmethod
    def get_stranger_conversation_list(
        auth,
        *,
        cursor: int = 0,
        count: int = 50,
        show_total_unread: bool = True,
        **kwargs,
    ) -> dict:
        url = "https://imapi.douyin.com/v1/stranger/get_conversation_list"
        request_proto = ProtoBuilder.build_get_stranger_conversation_list_request(
            auth,
            cursor=cursor,
            count=count,
            show_total_unread=show_total_unread,
        )
        return _request_im_proto(url, request_proto, auth=auth)

    @staticmethod
    def get_all_stranger_conversation_list(
        auth,
        *,
        count: int = 50,
        max_pages: int = 20,
        **kwargs,
    ) -> list:
        conversations = []
        cursor = 0
        for _ in range(max_pages):
            resp = DouyinAPI.get_stranger_conversation_list(auth, cursor=cursor, count=count)
            body = (resp.get("body") or {}).get("get_stranger_conversation_body") or {}
            chunk = body.get("conversation_list") or []
            if not chunk:
                break
            conversations.extend(chunk)
            if not body.get("has_more"):
                break
            next_cursor = body.get("next_cursor", 0)
            try:
                next_cursor = int(next_cursor)
            except (TypeError, ValueError):
                next_cursor = 0
            if next_cursor <= 0 or next_cursor == cursor:
                break
            cursor = next_cursor
        return conversations

    @staticmethod
    def get_stranger_messages(
        auth,
        *,
        conversation_short_id: int,
        reset_unread_count: bool = False,
        **kwargs,
    ) -> dict:
        url = "https://imapi.douyin.com/v1/stranger/get_messages"
        request_proto = ProtoBuilder.build_get_stranger_messages_request(
            auth,
            conversation_short_id=conversation_short_id,
            reset_unread_count=reset_unread_count,
        )
        return _request_im_proto(url, request_proto, auth=auth)

    @staticmethod
    def get_messages_by_conversation(
        auth,
        conversation_id: str,
        conversation_short_id: int,
        *,
        conversation_type: int = 1,
        anchor_index: int = 0,
        limit: int = 50,
        direction: int = 1,
        **kwargs,
    ):
        """获取私信历史消息。"""
        url = 'https://imapi.douyin.com/v1/message/get_by_conversation'
        request = ProtoBuilder.build_get_messages_by_conversation_request(
            auth,
            conversation_id=conversation_id,
            conversation_short_id=conversation_short_id,
            conversation_type=conversation_type,
            anchor_index=anchor_index,
            limit=limit,
            direction=direction,
        )
        return _request_im_proto(url, request, auth=auth)

    @staticmethod
    def send_im_content(
        auth,
        conversation_id,
        conversation_short_id,
        ticket,
        msg_content: dict,
        message_type: int,
        **kwargs,
    ):
        url = 'https://imapi.douyin.com/v1/message/send'
        headers = HeaderBuilder().build(HeaderType.PROTOBUF)
        headers.set_header('referer', 'https://www.douyin.com/')
        requestProto = ProtoBuilder.build_send_im_content_request(
            auth,
            conversation_id,
            conversation_short_id,
            ticket,
            msg_content,
            int(message_type),
        )
        params = {
            'verifyFp': auth.cookie['s_v_web_id'],
            'fp': auth.cookie['s_v_web_id'],
            'msToken': generate_msToken()
        }
        query = splice_url(params)
        abogus = generate_a_bogus(query)
        params['a_bogus'] = abogus
        resp = requests.post(
            url,
            params=params,
            headers=headers.get(),
            verify=False,
            cookies=auth.cookie,
            data=requestProto.SerializeToString(),
            timeout=kwargs.get("timeout", 20),
        )
        responseProto = ResponseProto.Response()
        responseProto.ParseFromString(resp.content)
        return protobuf_to_dict(responseProto)

    @staticmethod
    def send_msg(auth, conversation_id, conversation_short_id, ticket, content: str, **kwargs) -> bool:
        """
        发送私信.
        :param auth: DouyinAuth object.
        :param conversation_id: 私信对话ID.
        :param conversation_short_id: 私信对话短ID.
        :param ticket: 私信对话票据.
        :param content: 私信内容.
        :return: True 发送成功 False 发送失败
        """
        return DouyinAPI.send_im_content(
            auth,
            conversation_id,
            conversation_short_id,
            ticket,
            {
                "mention_users": [],
                "aweType": 700,
                "richTextInfos": [],
                "text": content,
            },
            7,
            **kwargs,
        )

    @staticmethod
    def send_emoji(auth, conversation_id, conversation_short_id, ticket, emoji_url: str, **kwargs):
        from utils.im_media_cache import build_douyin_emoji_payload

        payload = build_douyin_emoji_payload(emoji_url)
        if not payload:
            raise ValueError("invalid emoji url")
        return DouyinAPI.send_im_content(
            auth,
            conversation_id,
            conversation_short_id,
            ticket,
            payload,
            5,
            **kwargs,
        )

    @staticmethod
    def send_image(auth, conversation_id, conversation_short_id, ticket, image_path: str, **kwargs):
        from utils.im_media_cache import build_douyin_image_inline_payload

        payload = build_douyin_image_inline_payload(image_path)
        if not payload:
            raise ValueError("invalid image path")
        return DouyinAPI.send_im_content(
            auth,
            conversation_id,
            conversation_short_id,
            ticket,
            payload,
            27,
            **kwargs,
        )

    @staticmethod
    def send_msg_confirmed(
        auth,
        conversation_id,
        conversation_short_id,
        ticket,
        content: str,
        *,
        confirm_timeout: float = 8.0,
        poll_interval: float = 0.8,
        history_limit: int = 20,
    ):
        expected_text = str(content or "").strip()
        if not expected_text:
            raise DouyinAPIError("invalid_content", "empty message content")

        self_uid = str(auth.get_uid() or "").strip()
        if not self_uid:
            raise DouyinAPIError("missing_self_uid", "unable to resolve self uid")

        started_at_ms = int(time.time() * 1000)
        baseline_index = 0
        try:
            baseline_resp = DouyinAPI.get_messages_by_conversation(
                auth,
                conversation_id,
                conversation_short_id,
                limit=history_limit,
            )
            baseline_messages = _messages_from_conversation_response(baseline_resp)
            baseline = _find_confirmed_outbound_message(
                baseline_messages,
                self_uid=self_uid,
                expected_text="",
                min_index=-1,
                min_create_time_ms=0,
            )
            if isinstance(baseline, dict):
                baseline_index = _safe_int(baseline.get("index_in_conversation"), 0)
        except Exception:
            baseline_index = 0

        resp_json = DouyinAPI.send_msg(
            auth,
            conversation_id,
            conversation_short_id,
            ticket,
            expected_text,
        )
        error_desc = ""
        message_text = ""
        if isinstance(resp_json, dict):
            error_desc = str(resp_json.get("error_desc") or "").strip()
            message_text = str(resp_json.get("message") or "").strip()
        if error_desc:
            raise DouyinAPIError("send_error", error_desc, raw=resp_json)

        deadline = time.time() + max(0.5, float(confirm_timeout))
        last_error = ""
        last_self_message = None
        while time.time() < deadline:
            try:
                resp = DouyinAPI.get_messages_by_conversation(
                    auth,
                    conversation_id,
                    conversation_short_id,
                    limit=history_limit,
                )
                messages = _messages_from_conversation_response(resp)
                last_self_message = _find_confirmed_outbound_message(
                    messages,
                    self_uid=self_uid,
                    expected_text="",
                    min_index=baseline_index,
                    min_create_time_ms=started_at_ms - 2000,
                )
                confirmed = _find_confirmed_outbound_message(
                    messages,
                    self_uid=self_uid,
                    expected_text=expected_text,
                    min_index=baseline_index,
                    min_create_time_ms=started_at_ms - 2000,
                )
                if isinstance(confirmed, dict):
                    return {
                        "response": resp_json,
                        "confirmed_message": confirmed,
                    }
            except Exception as exc:
                last_error = str(exc).strip()
            time.sleep(max(0.1, float(poll_interval)))

        detail = error_desc or last_error
        if not detail:
            if message_text and message_text.upper() != "OK":
                detail = f"发送接口返回: {message_text}"
            else:
                detail = "发送接口返回OK，但在会话历史中未确认到该消息"
            last_self_summary = _summarize_im_message_for_confirm(last_self_message)
            if last_self_summary:
                detail = f"{detail}（最近自发消息: {last_self_summary}）"
        raise DouyinAPIError(
            "send_unconfirmed",
            detail or "message was not confirmed by conversation history",
            raw=resp_json,
        )

    @staticmethod
    def find_recent_outbound_by_text(
        auth,
        conversation_id,
        conversation_short_id,
        content: str,
        *,
        lookback_ms: int = 120000,
        history_limit: int = 30,
    ):
        """在会话历史中查找近期自发且正文匹配的消息（用于软成功防双发）。"""
        expected_text = str(content or "").strip()
        if not expected_text:
            return None
        self_uid = str(auth.get_uid() or "").strip()
        if not self_uid:
            return None
        min_create_time_ms = int(time.time() * 1000) - max(0, int(lookback_ms))
        try:
            resp = DouyinAPI.get_messages_by_conversation(
                auth,
                conversation_id,
                conversation_short_id,
                limit=history_limit,
            )
            messages = _messages_from_conversation_response(resp)
        except Exception:
            return None
        return _find_confirmed_outbound_message(
            messages,
            self_uid=self_uid,
            expected_text=expected_text,
            min_index=-1,
            min_create_time_ms=min_create_time_ms,
        )

    @staticmethod
    def get_device_id(auth, **kwargs) -> str:
        """
        获取设备ID.
        :param auth: DouyinAuth object.
        :return: 设备ID.
        """
        url = "https://www.douyin.com/aweme/v1/web/query/user"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = "https://www.douyin.com/discover"
        headers.set_header("referer", refer)
        params = (
            Params()
            .with_web_defaults(auth, refer, {"publish_video_strategy_type": "2"})
            .with_a_bogus()
        )
        resp = _request_json(
            "GET",
            url,
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )
        return resp["id"]

    @staticmethod
    def digg(auth, aweme_id: str, digg_type: str = '1', **kwargs) -> bool:
        """
        点赞视频.
        :param auth: DouyinAuth object.
        :param aweme_id: 视频ID.
        :param digg_type: 点赞类型, 1: 点赞, 0: 取消点赞.
        :return: 0 点赞成功 1 取消点赞成功
        """
        api = '/aweme/v1/web/commit/item/digg/'
        url = f'{DouyinAPI.douyin_url}{api}'
        refer = f'{DouyinAPI.douyin_url}/discover?modal_id={aweme_id}'
        headers = HeaderBuilder.build(HeaderType.FORM)
        headers.set_header("Host", DouyinAPI.douyin_url.split("https://")[-1])
        headers.with_bd(api, auth)
        headers.with_csrf(auth.cookie_str)
        headers.set_header("origin", DouyinAPI.douyin_url)
        headers.set_header("referer", refer)
        params = (
            Params()
            .with_web_defaults(auth, refer)
            .with_a_bogus()
        )
        data = {
            "aweme_id": aweme_id,
            "item_type": "0",
            "type": digg_type,
        }
        resp = _request_json(
            "POST",
            url,
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
            data=data,
        )
        return resp.get("is_digg") == 0

    @staticmethod
    def search_some_video_work(auth, query: str, num: int = 16, sort_type: str = '0', publish_time: str = '0',
                               filter_duration="", search_range="0", **kwargs) -> tuple:
        """
        搜索视频频道作品.
        :param auth: DouyinAuth object.
        :param query: 搜索关键字.
        :param num: 搜索结果数量.
        :param sort_type: 排序方式 0 综合排序 1 最多点赞 2 最新发布.
        :param publish_time: 发布时间 0 不限 1 一天内 7 一周内 180 半年内.
        :param filter_duration: 视频时长 空字符串 不限 0-1 一分钟内 1-5 1-5分钟内 5-10000 5分钟以上
        :param search_range: 搜索范围 0 不限 3 关注的人 1 最近看过 2 还未看过
        :return: 作品列表, 引导词.
        """
        offset = "0"
        count = "25"
        search_id = ""
        video_work_list = []
        while True:
            search_id, guide_search_words, res_json = DouyinAPI.search_video_work(auth, query, offset, count, sort_type,
                                                                                  publish_time, filter_duration,
                                                                                  search_range, search_id)
            video_works = res_json["data"]
            video_work_list.extend(video_works)
            if res_json["has_more"] != 1 or len(video_work_list) >= num:
                break
            offset = str(int(offset) + int(count))
        if len(video_work_list) > num:
            video_work_list = video_work_list[:num]
        return video_work_list, guide_search_words

    @staticmethod
    def search_video_work(auth, query: str, offset: str = '0', count: str = '16', sort_type: str = '0',
                          publish_time: str = '0', filter_duration="", search_range="0", search_id="", **kwargs):
        """
        搜索视频频道作品.
        :param auth: DouyinAuth object.
        :param query: 搜索关键字.
        :param offset: 搜索结果偏移量.
        :param count: 搜索结果数量.
        :param sort_type: 排序方式 0 综合排序 1 最多点赞 2 最新发布.
        :param publish_time: 发布时间 0 不限 1 一天内 7 一周内 180 半年内.
        :param filter_duration: 视频时长 空字符串 不限 0-1 一分钟内 1-5 1-5分钟内 5-10000 5分钟以上
        :param search_range: 搜索范围 0 不限 3 关注的人 1 最近看过 2 还未看过
        :return: 下个搜索ID, 引导词, JSON数据.
        """
        api = "/aweme/v1/web/search/item/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f'https://www.douyin.com/search/{urllib.parse.quote(query)}?aid={uuid.uuid4()}&type=video'
        headers.set_referer(refer)
        extra_params = {
            "search_channel": "aweme_video_web",
            "enable_history": "1",
            "sort_type": sort_type,
            "publish_time": publish_time,
            "filter_duration": filter_duration,
            "search_range": search_range,
            "keyword": query,
            "search_source": "normal_search",
            "query_correct_type": "1",
            "is_filter_search": "1",
            "from_group_id": "",
            "offset": offset,
            "count": count,
            "need_filter_settings": "1" if offset == "0" else "0",
            "list_type": "single",
            "round_trip_time": "50",
        }
        if search_id != "":
            extra_params["search_id"] = search_id
        params = (
            Params()
            .with_web_defaults(auth, refer, extra_params)
            .with_a_bogus()
        )
        resp = requests.get(
            f"{DouyinAPI.douyin_url}{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
            verify=_api_verify_tls(),
            timeout=20,
        )
        resp.raise_for_status()
        json_data = resp.json()
        new_search_id = resp.headers.get("X-Tt-Logid", "")
        return new_search_id, json_data.get("guide_search_words", []), json_data


if __name__ == '__main__':
    from utils.common_util import load_im_auth
    auth_, auth_source = load_im_auth()
    print(f"IM auth loaded from: {auth_source}")

    live_url = "https://live.douyin.com/852953608964"
    live_id = "852953608964"
    res = DouyinAPI.get_live_info(auth_, live_id)
    print(res)

    room_id = res['room_id']
    anchor_id = res['anchor_id']
    sec_anchor_id = res['sec_uid']
    DouyinAPI.get_rank_list(auth_, room_id, anchor_id, sec_anchor_id)



    # res = DouyinAPI.search_live(auth_, "三角洲")
    # # print(res)
    # for i in res['data']:
    #     print(i['lives']['author']['nickname'])
    #     live_id = re.findall(r'"web_rid":"(.*?)",', str(i['lives']))[0]
    #     live_url = f'https://live.douyin.com/{live_id}'
    #     print(live_url)

    # my_uid = DouyinAPI.get_my_uid(auth_)
    # print(my_uid)
    # my_sec_uid = DouyinAPI.get_my_sec_uid(auth_)
    # print(my_sec_uid)
    # work_url = r'https://www.douyin.com/video/7433523124836060416'
    # print(DouyinAPI.get_user_info(auth_, "https://www.douyin.com/user/MS4wLjABAAAA7BDbZk0LjnEMcDDsLag5mDrMc157hD3x0SMhH1HaCM8"))
    # print(DouyinAPI.digg(auth_, "7433523124836060416", "1"))
    # print(DouyinAPI.digg(auth_, "7212619184386182435", "1"))
    # user_info = DouyinAPI.get_user_info(auth_, "https://www.douyin.com/user/MS4wLjABAAAAHXtdycTLMSe5Ld_468-9HKR1HUUrk4ywq-xMCM-E9w_cDIrhmynrQUalv061ZSpn?from_tab_name=main")
    # to_user_id = user_info['user']['uid']
    # conversation_id, conversation_short_id, ticket = DouyinAPI.create_conversation(auth_, to_user_id)
    # content = r'有份长期通告寻求合作，你通过了前期筛选，我是项目负责人，期待你与我联系：ncyj12'
    # DouyinAPI.send_msg(auth_, conversation_id, conversation_short_id, ticket, content)
    # print(DouyinAPI.get_user_all_work_info(auth_,"https://www.douyin.com/user/MS4wLjABAAAA8nC7nKxMrRtBwEqFzRgRBSxhBcw89VL0ysN-IXvhlKU?vid=7378825215213718818"))
    # print(DouyinAPI.get_work_info(auth_, "https://www.douyin.com/video/7212619184386182435"))
    # print(DouyinAPI.get_work_all_out_comment(auth_, "https://www.douyin.com/video/7212619184386182435"))
    # print(DouyinAPI.get_work_inner_comment(auth_, {
    #     "aweme_id": "7212619184386182435",
    #     "cid": "7327990109411902208"
    # }, "0"))
    # print(DouyinAPI.get_work_all_inner_comment(auth_, {
    #     "aweme_id": "7212619184386182435",
    #     "cid": "7327990109411902208"
    # }))
    # print(DouyinAPI.get_work_all_comment(auth_, "https://www.douyin.com/video/7212619184386182435"))
    # print(DouyinAPI.search_general_work(auth_, "美女", sort_type='2'))
    # print(DouyinAPI.search_some_general_work(auth_, "美女", sort_type='2', publish_time='0', num=30))
    # print(DouyinAPI.get_all_live_production(auth_, "https://live.douyin.com/84255891276"))
    # 60503986163 289606013148 91819894158
    # room_info = DouyinAPI.get_live_info(auth_, '60503986163')
    # print(room_info)
    # print(DouyinAPI.get_live_production(auth_, "https://live.douyin.com/84255891276", room_id, author_id, '0'))
    # print(DouyinAPI.collect_aweme(auth_, "7377676120549772554", '1'))
    # print(DouyinAPI.move_collect_aweme(auth_, "7207861673711930656", "tt", "7379252593215919891"))
    # print(DouyinAPI.remove_collect_aweme(auth_, "7376244589235113250", "tt", "7379252593215919891"))
    # print(DouyinAPI.get_live_production_detail(auth_, "https://live.douyin.com/552370739330", "3622058069401408240", "MS4wLjABAAAATfhR-kvE-AWqZaNaomCLFqgDKzvBwMS87FUGVjS_u7Y", "7379220637308504843"))
    # print(DouyinAPI.get_collect_list(auth_))
    # print(DouyinAPI.search_user(auth_, "巴旦木公主"))
    # print(DouyinAPI.search_some_user(auth_, "巴旦木公主", 30))
    # print(DouyinAPI.search_live(auth_, "馨馨baby😐ᵇᵃᵇʸ"))
    # print(DouyinAPI.get_user_favorite(auth_, "MS4wLjABAAAA99bTJ_GOw3odYmsXOe7i7xuEv0iQf2X_Kg_VUyVP0U8"))
    # print(DouyinAPI.get_some_user_follower_list(auth_, "3074704605975950", "MS4wLjABAAAA0L4jpkJDeuFO9AM-dQK1B649tmr7GIw-sQtyPasP_Z45QnUjIQgUOLIs8Kw8Gp-u", 40))
    # print(DouyinAPI.get_some_user_following_list(auth_, "3074704605975950", "MS4wLjABAAAA0L4jpkJDeuFO9AM-dQK1B649tmr7GIw-sQtyPasP_Z45QnUjIQgUOLIs8Kw8Gp-u", 40))
    # print(DouyinAPI.search_some_video_work(auth_, "巴旦木公主", 32))
    # print(DouyinAPI.get_feed(auth_))
    # print(DouyinAPI.publish_comment(auth_, "7356193166732709139"))
    # print(DouyinAPI.get_upload_auth_key(auth_))

    # while True:
    #     print(DouyinAPI.sendMsgInRoom(auth_, room_id, "666"))
    #     time.sleep(3)
    # #
    # while True:
    #     print(DouyinAPI.diggLiveRoom(auth_, room_id, '10'))
    #     time.sleep(1)
