/**
 * `lib/api.ts` 的契约行为测试。
 *
 * 这里测的不是「有没有发出请求」，而是**前端对后端响应的解释方式**——
 * 两处最容易出错的地方：
 * 1. 后端用空对象 `{}` 表示「无情绪 / 无播报指令」，若不规范化成 null，
 *    `MoodIndicator` 会拿到一个字段全缺的伪对象并算出 NaN%；
 * 2. 知识库上传判重返回 409，`detail` 是**结构体**而非字符串，
 *    界面需要读它渲染「覆盖 / 取消」，所以必须原样挂到 ApiError 上。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, deleteSession, getSessionHistory, getSessions, postChat, uploadKnowledge } from "@/lib/api";

/** 构造一个最小的 fetch 响应替身（只提供 api.ts 实际用到的成员）。 */
function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

/** 记录最后一次 fetch 的入参，便于断言请求形状。 */
function stubFetch(response: Response) {
  const fetchMock = vi.fn(async () => response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("postChat：空对象的规范化", () => {
  it("后端用 {} 表示「无情绪 / 无播报指令」时，规范化为 null", async () => {
    stubFetch(
      jsonResponse({
        session_id: "s1",
        persona_id: "p1",
        reply: "嗯，我在。",
        emotion: {},
        speak: {},
      }),
    );

    const res = await postChat({ text: "在吗" });

    expect(res.emotion).toBeNull();
    expect(res.speak).toBeNull();
  });

  it("情绪与播报指令有内容时原样保留", async () => {
    const emotion = { label: "anxious", label_zh: "焦虑", intensity: 0.7 };
    const speak = { ssml: "<speak>嗯</speak>", display_text: "嗯" };
    stubFetch(jsonResponse({ session_id: "s1", reply: "嗯", emotion, speak }));

    const res = await postChat({ text: "在吗" });

    expect(res.emotion).toEqual(emotion);
    expect(res.speak).toEqual(speak);
  });

  it("非 2xx 时抛出带状态码与后端 detail 的 ApiError", async () => {
    stubFetch(jsonResponse({ detail: "请提供 file 或 text" }, 422));

    await expect(postChat({ text: "在吗" })).rejects.toMatchObject({
      name: "ApiError",
      status: 422,
      detail: "请提供 file 或 text",
      message: expect.stringContaining("请提供 file 或 text"),
    });
  });
});

describe("会话历史接口", () => {
  it("列表带上 persona_id 过滤（会话按陪伴对象隔离）", async () => {
    const fetchMock = stubFetch(jsonResponse([]));

    await getSessions("苏澄 角色");

    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    const parsed = new URL(url);
    expect(parsed.pathname).toBe("/chat/sessions");
    expect(parsed.searchParams.get("persona_id")).toBe("苏澄 角色");
  });

  it("历史消息路径对 session_id 做 URL 编码", async () => {
    const fetchMock = stubFetch(
      jsonResponse({ session_id: "x", persona_id: "p", user_name: "朋友", messages: [] }),
    );

    await getSessionHistory("a/b c");

    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    expect(url).toContain("/chat/sessions/a%2Fb%20c/history");
  });

  it("会话不存在时抛出 404 的 ApiError（调用方据此退回新对话）", async () => {
    stubFetch(jsonResponse({ detail: "会话不存在：gone" }, 404));

    await expect(getSessionHistory("gone")).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
    });
  });

  it("删除对话：DELETE + persona_id 归属参数（会话 id 做 URL 编码）", async () => {
    const fetchMock = stubFetch(jsonResponse({ session_id: "s/1", removed_turns: 4 }));

    const result = await deleteSession("s/1", "苏澄 角色");

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const parsed = new URL(url);
    expect(parsed.pathname).toBe("/chat/sessions/s%2F1");
    expect(parsed.searchParams.get("persona_id")).toBe("苏澄 角色");
    expect(init.method).toBe("DELETE");
    expect(result.removed_turns).toBe(4);
  });
});

describe("uploadKnowledge：判重（409）", () => {
  it("把结构体 detail 原样挂到 ApiError 上（界面据此渲染覆盖确认）", async () => {
    const detail = {
      reason: "similar",
      similarity: 0.9123,
      existing: { doc_id: "d1", title: "旧文档", chunk_count: 12 },
      hint: "如需覆盖，请带 force=true 重新提交",
    };
    stubFetch(jsonResponse({ detail }, 409));

    const error = await uploadKnowledge({ companionId: "c1", text: "一些内容" }).catch(
      (err: unknown) => err,
    );

    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(409);
    expect(apiError.detail).toEqual(detail);
  });

  it("companion_id 走 query，正文走 multipart，且不手动设置 Content-Type", async () => {
    const fetchMock = stubFetch(jsonResponse({ doc: { doc_id: "d1" } }));

    await uploadKnowledge({ companionId: "苏澄 角色", text: "一段文本", force: true });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    // 解析后比对值，而不是比对编码细节（URLSearchParams 会把空格编成 "+"）
    const parsed = new URL(url);
    expect(parsed.pathname).toBe("/knowledge/upload");
    expect(parsed.searchParams.get("companion_id")).toBe("苏澄 角色");
    expect(init.method).toBe("POST");
    // 设了 Content-Type 会破坏 multipart 边界，必须交给浏览器补
    expect(init.headers).toBeUndefined();
    const form = init.body as FormData;
    expect(form.get("text")).toBe("一段文本");
    expect(form.get("force")).toBe("true");
  });
});
