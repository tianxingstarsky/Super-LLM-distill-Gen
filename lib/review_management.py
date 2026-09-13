"""Infrequent import and identity setup, outside the continuous review workspace."""
from __future__ import annotations

import re
from pathlib import Path

import streamlit as st

from lib import review_center as rc

ROOT = Path(__file__).resolve().parent.parent


def reviewer_identity():
    key = st.session_state.get('review-auth-key')
    if key:
        return rc.authenticate(key)['username']
    rc.ensure_admin()
    return 'admin'


def render_management(mode, dataset, select_samples):
    if st.button('返回审核工作台', type='primary'):
        st.session_state.pop('review-management', None)
        st.rerun()
    if mode == 'import':
        st.title('导入待审样本')
        st.caption('从已打开文件夹读取，不搬动、不覆盖原文件。导入后再进入连续审核。')
        try:
            username = reviewer_identity()
        except PermissionError:
            st.warning('个人密钥已失效，请返回工作台并重新验证身份。')
            return
        if username != 'admin':
            st.info('导入由本机管理员执行。协作者可在审核工作台审阅已授权的数据集。')
            return
        _, samples = select_samples('review-import-source')
        if samples:
            st.write(f'检测到 {len(samples)} 条样本，目标数据集：{dataset}')
            if st.button('将所选样本加入待审'):
                from lib.review import push_samples
                push_samples(samples, {}, dataset_name=dataset)
                st.success('样本已加入待审，原始文件未修改。')
        return

    st.title('审核身份与协作者接入')
    try:
        identity = reviewer_identity()
    except PermissionError:
        identity = None
        st.warning('个人密钥已失效，请重新验证或退出该身份。')
    st.write(f'当前身份：**{identity or "未认证"}**' + ('（本机管理员入口）' if identity == 'admin' else '（密钥认证）'))
    st.caption('身份由密钥验证，不通过填写用户名冒充。每个人的待审队列与审核票独立。')
    with st.form('review-sign-in', clear_on_submit=True):
        key = st.text_input('本审核中心的个人 API 密钥', type='password')
        login = st.form_submit_button('验证并切换审核身份')
    if login:
        try:
            who = rc.authenticate(key)
            rc.queue_page(dataset, who['username'], limit=1)
            st.session_state['review-auth-key'] = key
            st.session_state.pop('review-management', None)
            st.query_params.pop('record', None)
            st.rerun()
        except (ValueError, PermissionError) as error:
            st.error(str(error))
    if st.session_state.get('review-auth-key') and st.button('退出个人身份，返回本机管理入口'):
        st.session_state.pop('review-auth-key', None)
        st.query_params.pop('record', None)
        st.rerun()
    st.divider()
    st.subheader('配置自己主机上的审核 Agent')
    st.caption('这是远程 Agent 接入配置，不会更改上方浏览器会话的身份。配置只保存到本机私有文件。')
    with st.form('collaborator-setup', clear_on_submit=True):
        name = st.text_input('配置名称', 'review_remote.local')
        server = st.text_input('中心地址', 'http://127.0.0.1:6900')
        remote_key = st.text_input('远程 API 密钥', type='password')
        model = st.text_input('评审模型（可留空）')
        policy = st.selectbox('审核规则', ['quality', 'safety'])
        save = st.form_submit_button('验证并保存配置')
    if save:
        if not re.fullmatch(r'review_remote\.[a-zA-Z0-9_-]+', name):
            st.error('名称格式：review_remote.自定义名称')
            return
        try:
            import yaml
            from lib.review_remote import AuthClient
            client = AuthClient(server, remote_key)
            client.pending(dataset, batch=1)
            target = ROOT / 'configs' / (name + '.yaml')
            with target.open('x', encoding='utf-8') as handle:
                yaml.safe_dump({'server': server, 'api_key': remote_key, 'model': model,
                                'policy': policy, 'dataset': dataset}, handle, allow_unicode=True)
            st.success(f"已验证账号 {client.me['username']}，保存到 {target.name}，未覆盖已有配置。")
        except (OSError, ValueError, ConnectionError) as error:
            st.error(str(error))
