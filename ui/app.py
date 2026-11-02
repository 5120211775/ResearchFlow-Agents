"""Single Python interface; all business operations go through FastAPI."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ui.api_client import APIClient, APIError
from ui.state import LIVE, label, merge_events, submission_key

st.set_page_config(page_title="研究助手", page_icon="🔎", layout="wide")


def client() -> APIClient:
    if "api" not in st.session_state:
        st.session_state.api = APIClient(os.getenv("API_BASE_URL", "http://127.0.0.1:8000"))
    return st.session_state.api


def show_error(exc: APIError):
    st.error(str(exc))
    if exc.status == 401:
        st.session_state.pop("user", None)
        st.warning("登录已过期，请重新登录。")


def auth():
    st.title("多 Agent 研究助手")
    st.caption("问题 → 计划审批 → 检索与评价 → 报告审批 → 项目记忆")
    try:
        if client().health().get("status") == "ready":
            st.caption("API、数据库和 Redis 已就绪")
    except APIError as exc:
        st.warning(str(exc))
    with st.form("auth"):
        email = st.text_input("邮箱", key="email")
        password = st.text_input("密码", type="password", key="password")
        login = st.form_submit_button("登录", type="primary")
        register = st.form_submit_button("注册账号")
    if register:
        client().post("/auth/register", email=email, password=password)
        st.success("注册请求已处理，请登录。密码至少 12 位，避免常见弱密码。")
    if login:
        st.session_state.user = client().login(email, password)
        st.rerun()


def projects() -> str | None:
    rows = client().get("/projects")["projects"]
    with st.sidebar:
        st.write(st.session_state.user["email"])
        if st.button("退出登录"):
            try:
                client().logout()
            finally:
                client().close()
                for key in list(st.session_state):
                    del st.session_state[key]
            st.rerun()
        ids = [r["id"] for r in rows]
        names = {r["id"]: r["name"] for r in rows}
        if st.session_state.get("project") not in ids:
            st.session_state.pop("project", None)
        selected = (
            st.selectbox("当前项目", ids, format_func=names.get, key="project") if ids else None
        )
        with st.expander("新建项目", expanded=not rows):
            with st.form("new_project", clear_on_submit=True):
                name = st.text_input("项目名称")
                description = st.text_area("项目说明")
                if st.form_submit_button("创建项目"):
                    if name.strip():
                        client().post("/projects", name=name.strip(), description=description)
                        st.session_state.pop("project", None)
                        st.rerun()
        if selected:
            runtime = client().get("/runtime")
            st.info(
                "真实模型模式"
                if runtime["llm_mode"] == "real"
                else "Fake 演示模式：不会调用真实模型"
            )
            with st.expander("当前配置（无密钥）"):
                st.json(runtime)
    return selected


def documents(project: str):
    st.header("项目与知识库")
    status = client().get(f"/projects/{project}/corpus/status")
    st.caption(
        f"当前索引模型：{status['current_model']} · 文档 {status['documents']} · 块 {status['chunks']}"
    )
    st.caption("上传接口同步完成解析和索引；仅在返回成功后显示索引完成。不同模型的索引分别统计。")
    with st.form("upload", clear_on_submit=True):
        file = st.file_uploader("上传学习资料")
        submit = st.form_submit_button("上传并索引", type="primary")
    if submit and file:
        with st.spinner("解析与索引中，请等待接口返回…"):
            result = client().upload(project, file.name, file.getvalue())
        st.success(f"索引完成：{result['filename']}，{result['chunks']} 个块")
    docs = client().get(f"/projects/{project}/corpus/documents")
    if docs:
        st.dataframe(
            [
                {k: d.get(k) for k in ("filename", "chunks", "origin", "version", "is_current")}
                for d in docs
            ],
            width="stretch",
        )
        with st.expander("索引模型分布"):
            st.json(status["chunks_by_model"])
    else:
        st.info("尚无文档。配置 Embedding 后上传；仅网络研究不要求先上传资料。")


def new_research(project: str):
    st.header("发起研究")
    st.caption("提交后由 Celery 在后台执行；页面刷新不会重新提交。计划必须先审批。")
    docs = [
        d
        for d in client().get(f"/projects/{project}/corpus/documents")
        if d.get("is_current", True) and d.get("origin") == "uploaded"
    ]
    pending = st.session_state.get("uncertain_submission", False)
    with st.form("research"):
        question = st.text_area("研究问题", height=120)
        depth = st.selectbox("研究深度", ["fast", "balanced", "comprehensive"])
        scope = st.radio("研究范围", ["网络搜索", "仅项目文档", "选中文档"], horizontal=True)
        doc_names = {d["doc_key"]: d["filename"] for d in docs if d.get("doc_key")}
        selected = st.multiselect(
            "选择文档（仅选中文档模式使用）", list(doc_names), format_func=doc_names.get
        )
        submit = st.form_submit_button("生成研究计划", type="primary", disabled=pending)
    if pending:
        st.warning("上次提交结果未知。先到研究工作台检查历史，确认未创建任务后再解除锁定。")
        if st.button("已检查历史，解除提交锁定"):
            st.session_state.uncertain_submission = False
            st.rerun()
    if submit:
        if not question.strip() or scope == "选中文档" and not selected:
            st.warning("请填写问题，选中文档模式至少选择一个文档。")
            return
        body = dict(
            project_id=project,
            question=question.strip(),
            depth=depth,
            skip_plan_gate=False,
            corpus_mode=scope != "网络搜索",
        )
        if scope == "选中文档":
            body["corpus_selection"] = {"doc_keys": selected, "folder_ids": []}
        fingerprint = submission_key(body)
        if st.session_state.get("submitted_key") == fingerprint:
            st.info("相同内容已经提交，请在工作台查看。更改问题可创建新任务。")
            return
        st.session_state.uncertain_submission = True
        try:
            result = client().post("/runs", **body)
        except APIError as exc:
            if not exc.uncertain:
                st.session_state.uncertain_submission = False
            raise
        st.session_state.uncertain_submission = False
        st.session_state.submitted_key = fingerprint
        st.session_state.current_run = result["run_id"]
        st.success(f"任务已提交：{result['run_id']}。请切换到研究工作台。")


def plan_gate(run_id: str, data: dict):
    plan = data["plans"][-1]
    st.subheader("计划审批")
    st.caption(f"计划版本 {plan['version']}。可取消任务或编辑查询，至少保留一个任务。")
    with st.form(f"plan_{run_id}_{plan['version']}"):
        tasks = []
        for pos, task in enumerate(plan["tasks"]):
            include = st.checkbox(
                f"任务 {task.get('id', pos + 1)}",
                value=task.get("include", True),
                key=f"keep_{run_id}_{pos}",
            )
            query = st.text_input(
                "检索任务", value=task.get("query", ""), key=f"query_{run_id}_{pos}"
            )
            tasks.append({**task, "include": include, "query": query})
        feedback = st.text_area("计划备注")
        approve = st.form_submit_button("批准计划，开始研究", type="primary")
        reject = st.form_submit_button("拒绝计划")
    if approve or reject:
        client().post(
            f"/runs/{run_id}/plan-review",
            decision="APPROVED" if approve else "REJECTED",
            tasks=tasks,
            feedback=feedback,
        )
        st.rerun()


def report_gate(run_id: str, data: dict):
    if not data["revisions"]:
        return
    latest = data["revisions"][-1]
    st.subheader(f"报告 · 版本 {latest['version']}")
    st.markdown(latest["report_markdown"])
    if data["run"]["status"] == "AWAITING_REVIEW":
        with st.form(f"review_{run_id}_{latest['version']}"):
            feedback = st.text_area("审核意见（请求重写时必填）")
            approve = st.form_submit_button("批准报告", type="primary")
            rework = st.form_submit_button("反馈重写")
        if approve or rework:
            if rework and not feedback.strip():
                st.warning("请填写重写意见。")
            else:
                with st.spinner("提交审核；批准后的记忆摄取可能需要等待…"):
                    client().post(
                        f"/runs/{run_id}/report-review",
                        revision_version=latest["version"],
                        decision="APPROVED" if approve else "REWORK_REQUESTED",
                        feedback=feedback,
                    )
                st.rerun()
    if data.get("artifact"):
        st.success("报告已批准，最终工件已冻结。记忆索引状态请到项目记忆页单独检查。")
        if st.button("为当前模型补建报告记忆（调用 Embedding）"):
            with st.spinner("检查并摄取批准报告…"):
                st.json(client().post(f"/runs/{run_id}/memory-index"))
        for format_, mime in [
            ("md", "text/markdown"),
            ("pdf", "application/pdf"),
            ("json", "application/json"),
        ]:
            key = f"export_{run_id}_{format_}"
            if st.button(f"准备 {format_.upper()} 导出", key=key + "_button"):
                st.session_state[key] = client().download(run_id, format_)
            if key in st.session_state:
                st.download_button(
                    f"下载 {format_.upper()}",
                    st.session_state[key],
                    f"research-{run_id}.{format_}",
                    mime,
                    key=key + "_download",
                    on_click="ignore",
                )
        if st.button("检查工件完整性"):
            result = client().get(f"/runs/{run_id}/verification")
            st.json(result)
            st.caption("通过表示完整性和关联一致，不表示所有事实正确。")


def workspace(project: str):
    st.header("研究工作台")
    rows = client().get("/runs", project_id=project, limit=200)["runs"]
    if not rows:
        st.info("没有研究任务，请先发起研究。")
        return
    ids = [r["id"] for r in rows]
    names = {r["id"]: f"{label(r['status'])} · {r['question'][:55]} · {r['id'][:8]}" for r in rows}
    current = st.session_state.get("current_run")
    selected = st.selectbox(
        "选择当前或历史任务",
        ids,
        index=ids.index(current) if current in ids else 0,
        format_func=names.get,
    )
    st.session_state.current_run = selected
    initial = client().get(f"/runs/{selected}")

    @st.fragment(run_every=3 if initial["run"]["status"] in LIVE else None)
    def panel():
        try:
            data = client().get(f"/runs/{selected}")
            run = data["run"]
            st.subheader(label(run["status"]))
            st.caption(f"Run ID: {selected} · {run['question']}")
            st.write(f"输入 Token：{run['tokens_input']} · 输出 Token：{run['tokens_output']}")
            st.caption("Custom Provider 的 cost=0 可能未计价；失败任务 Token 可能尚未完整落库。")
            with st.expander("实际任务模型与审批记录"):
                st.json(
                    {
                        "models": run["model_routing"],
                        "demo": run["demo"],
                        "reviews": data["reviews"],
                    }
                )
            event_key = f"events_{selected}"
            old = st.session_state.get(event_key, [])
            cursor = old[-1]["id"] if old else 0
            incoming = client().get(f"/runs/{selected}/events", after_id=cursor)["events"]
            events = merge_events(old, incoming)
            st.session_state[event_key] = events
            with st.expander("Agent 执行与工具记录", expanded=run["status"] in LIVE):
                for event in events[-80:]:
                    payload = event["payload"]
                    st.write(
                        f"{payload.get('agent') or payload.get('type', '')} · {payload.get('message') or payload.get('type', '')}"
                    )
                    if payload.get("detail"):
                        with st.expander(f"详情 #{event['id']}"):
                            st.json(payload["detail"])
            if run.get("error_message"):
                st.error(run["error_message"])
            if run["status"] == "AWAITING_PLAN":
                plan_gate(selected, data)
            if run["status"] in LIVE and st.button("取消研究"):
                client().post(f"/runs/{selected}/cancel")
                st.rerun()
            report_gate(selected, data)
            with st.expander(f"来源与证据（{len(data['sources'])} / {len(data['evidence'])}）"):
                for source in data["sources"]:
                    st.write(f"[{source.get('citation_index') or '未引用'}] {source['title']}")
                    st.code(source["url"], language=None)
                    for evidence in data["evidence"]:
                        if evidence["source_id"] == source["id"]:
                            st.write(evidence["snippet"] or "空片段")
                            st.caption(evidence["provenance_state"])
            if initial["run"]["status"] in LIVE and run["status"] not in LIVE:
                st.rerun()  # rebuild the fragment without its polling timer
            if st.button("刷新任务"):
                st.rerun()
        except APIError as exc:
            show_error(exc)

    panel()


def memory_chat(project: str):
    st.header("项目记忆与问答")
    status = client().get(f"/projects/{project}/memory/status")
    st.json(status)
    st.caption("已批准报告与当前模型索引分别统计；pending_reports 表示尚未被当前模型索引。")
    rows = client().get(f"/projects/{project}/threads")["threads"]
    if st.button("新建对话"):
        result = client().post(f"/projects/{project}/threads")
        st.session_state.current_thread = result["id"]
        st.rerun()
    if not rows:
        st.info("先创建对话；文档问答使用项目文档，报告问答使用批准报告记忆。")
        return
    ids = [r["id"] for r in rows]
    names = {r["id"]: r["title"] for r in rows}
    current = st.session_state.get("current_thread")
    thread = st.selectbox(
        "对话", ids, index=ids.index(current) if current in ids else 0, format_func=names.get
    )
    st.session_state.current_thread = thread
    scope = st.selectbox(
        "问答范围",
        ["report", "corpus", "everything", "web"],
        format_func={
            "report": "批准报告记忆",
            "corpus": "项目文档",
            "everything": "报告、文档与网络",
            "web": "网络",
        }.get,
    )
    for message in client().get(f"/threads/{thread}/messages"):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("citations"):
                with st.expander("引用报告与片段"):
                    st.json(message["citations"])
    question = st.chat_input("围绕当前项目提问")
    if question:
        with st.chat_message("user"):
            st.write(question)
        with st.chat_message("assistant"):
            placeholder = st.empty()
            text, done = "", False
            for event in client().chat(thread, question, scope):
                if event.get("type") == "chunk":
                    text += event.get("text", "")
                    placeholder.markdown(text)
                elif event.get("type") == "done":
                    done = True
            if not done:
                st.warning("连接已结束但没有完成标记，请刷新对话历史确认结果。")
            else:
                st.rerun()


def main():
    try:
        if not st.session_state.get("user"):
            auth()
            return
        project = projects()
        if not project:
            st.info("请在侧栏创建项目。")
            return
        page = st.sidebar.radio(
            "功能", ["发起研究", "研究工作台", "项目与知识库", "项目记忆与问答"], key="page"
        )
        {
            "发起研究": new_research,
            "研究工作台": workspace,
            "项目与知识库": documents,
            "项目记忆与问答": memory_chat,
        }[page](project)
    except APIError as exc:
        show_error(exc)


if __name__ == "__main__":
    main()
