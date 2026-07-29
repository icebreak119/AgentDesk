#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time : 2024/6/8 下午6:57
# @Author : crush0
# @Description :
import base64
import json
import random

import uuid

import static.Request_pb2 as RequestProto
from builder.header import HeaderBuilder
from utils.dy_util import generate_webid, generate_req_sign, generate_millisecond


class ProtoBuilder:
    @staticmethod
    def build_normal_request(auth, cmd):
        request = RequestProto.Request()
        request.cmd = cmd
        request.sequence_id = random.randint(10000, 11000)
        request.sdk_version = "1.1.3"
        request.token = auth.ticket
        request.refer = 3
        request.inbox_type = 0
        request.build_number = "5fa6ff1:Detached: 5fa6ff1111fd53aafc4c753505d3c93daad74d27"
        request.device_id = '0'
        request.device_platform = 'douyin_pc'
        request.headers['session_aid'] = '6383'
        request.headers['session_did'] = '0'
        request.headers['app_name'] = 'douyin_pc'
        request.headers['priority_region'] = 'cn'
        request.headers['user_agent'] = HeaderBuilder.ua
        request.headers['cookie_enabled'] = 'true'
        request.headers['browser_language'] = 'zh-CN'
        request.headers['browser_platform'] = 'Win32'
        request.headers['browser_name'] = 'Mozilla'
        request.headers['browser_version'] = HeaderBuilder.ua.split('Mozilla/')[-1]
        request.headers['browser_online'] = 'true'
        request.headers['screen_width'] = '1707'
        request.headers['screen_height'] = '960'
        request.headers['referer'] = ''
        request.headers['timezone_name'] = 'Etc/GMT-8'
        request.headers['deviceId'] = '0'
        request.headers['webid'] = generate_webid()
        request.headers['fp'] = auth.cookie['s_v_web_id']
        request.headers['is-retry'] = '0'
        request.auth_type = 4
        request.biz = 'douyin_web'
        request.access = 'web_sdk'
        request.ts_sign = auth.ts_sign
        request.sdk_cert = base64.b64encode(auth.client_cert.encode('utf-8')).decode('utf-8')
        return request

    @staticmethod
    def build_create_conversation_request(auth, toId, myId):
        request = ProtoBuilder.build_normal_request(auth, 609)
        request.body.create_conversation_v2_body.conversation_type = 1
        request.body.create_conversation_v2_body.participants.extend([int(toId), int(myId)])
        reuqest_sign = generate_req_sign({
            "sign_data": f"avatar_url=&idempotent_id=&name=&participants={toId},{myId}",
            "certType": "cookie",
            "scene": "web_protect"
        }, auth.private_key)
        request.reuqest_sign = reuqest_sign
        return request

    @staticmethod
    def build_get_conversation_list_info_request(auth, toId, myId, conversation_short_id):
        request = ProtoBuilder.build_normal_request(auth, 610)
        request.body.get_conversation_info_list_v2_body.data.conversation_id = f"0:1:{myId}:{toId}"
        request.body.get_conversation_info_list_v2_body.data.conversation_short_id = int(conversation_short_id)
        request.body.get_conversation_info_list_v2_body.data.conversation_type = 1
        return request

    @staticmethod
    def build_get_user_conversation_list_request(
        auth,
        *,
        cursor=0,
        limit=20,
        conversation_type=1,
        sort_type=1,
        with_cold=False,
        include_role=None,
        exclude_role=None,
        push_status=None,
    ):
        request = ProtoBuilder.build_normal_request(auth, 2006)
        body = request.body.get_conversation_list_body
        body.sort_type = int(sort_type)
        body.cursor = int(cursor)
        body.con_type = int(conversation_type)
        body.limit = int(limit)
        body.with_cold = bool(with_cold)
        if include_role is not None:
            body.include_role = int(include_role)
        if exclude_role is not None:
            body.exclude_role = int(exclude_role)
        if push_status is not None:
            body.push_status = int(push_status)
        return request

    @staticmethod
    def build_get_stranger_conversation_list_request(auth, *, cursor=0, count=50, show_total_unread=True):
        request = ProtoBuilder.build_normal_request(auth, 1001)
        body = request.body.get_stranger_conversation_body
        body.cursor = int(cursor)
        body.count = int(count)
        body.show_total_unread = bool(show_total_unread)
        return request

    @staticmethod
    def build_get_stranger_messages_request(auth, *, conversation_short_id, reset_unread_count=False):
        request = ProtoBuilder.build_normal_request(auth, 1002)
        body = request.body.get_stranger_messages_body
        body.conversation_short_id = int(conversation_short_id)
        body.reset_unread_count = bool(reset_unread_count)
        return request

    @staticmethod
    def build_get_messages_by_conversation_request(
        auth,
        *,
        conversation_id,
        conversation_short_id,
        conversation_type=1,
        anchor_index=0,
        limit=50,
        direction=1,
    ):
        request = ProtoBuilder.build_normal_request(auth, 301)
        body = request.body.messages_in_conversation_body
        body.conversation_id = str(conversation_id or "").strip()
        body.conversation_type = int(conversation_type)
        body.conversation_short_id = int(conversation_short_id)
        body.direction = int(direction)
        body.anchor_index = int(anchor_index)
        body.limit = int(limit)
        return request

    @staticmethod
    def build_send_im_content_request(
        auth,
        conversation_id,
        conversation_short_id,
        ticket,
        msg_content: dict,
        message_type: int,
    ):
        client_message_id = str(uuid.uuid4())
        request = ProtoBuilder.build_normal_request(auth, 100)
        content_json = json.dumps(msg_content, ensure_ascii=False, separators=(',', ':'))
        request.body.send_message_body.conversation_id = conversation_id
        request.body.send_message_body.conversation_type = 1
        conversation_short_id = int(conversation_short_id)
        request.body.send_message_body.conversation_short_id = conversation_short_id
        request.body.send_message_body.content = content_json
        request.body.send_message_body.ext.append(
            RequestProto.ExtValue(key='s:client_message_id', value=client_message_id)
        )
        request.body.send_message_body.ext.append(
            RequestProto.ExtValue(key='s:stime', value=str(generate_millisecond()))
        )
        request.body.send_message_body.ext.append(
            RequestProto.ExtValue(key='s:mentioned_users', value='')
        )
        request.body.send_message_body.message_type = int(message_type)
        request.body.send_message_body.ticket = ticket
        request.body.send_message_body.client_message_id = client_message_id
        req_sign = generate_req_sign({
            "sign_data": f'content={content_json}&conversation_id={conversation_id}&conversation_short_id={conversation_short_id}',
            "certType": "cookie",
            "scene": "web_protect"
        }, auth.private_key)
        request.reuqest_sign = req_sign
        return request

    @staticmethod
    def build_send_message_request(auth, conversation_id, conversation_short_id, ticket, message):
        msg_content = {
            "mention_users": [],
            "aweType": 700,
            "richTextInfos": [],
            "text": message
        }
        return ProtoBuilder.build_send_im_content_request(
            auth,
            conversation_id,
            conversation_short_id,
            ticket,
            msg_content,
            7,
        )
