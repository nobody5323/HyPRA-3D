/**
 * SchemaForm 测试：JSON Schema → 表单的渲染子集、降级策略与值语义。
 *
 * 重点覆盖两类容易出错的地方：
 * ① **降级不丢数据**——渲染不了的类型必须只读展示，而不是渲染成空输入框后
 *    在保存时把字段抹掉（那会静默毁掉用户配置）；
 * ② **空值删键**——路径这类字段「清空」的语义是「不配置」，留个 `""` 会让后端
 *    把它当成一个真实的空路径。
 */

import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SchemaForm, missingRequired } from "@/components/settings/SchemaForm";
import type { PluginSettingsSchema } from "@/lib/api/types";

afterEach(cleanup);

/** 受控包装：把内部值吐到 DOM，便于断言「改完之后 values 到底长什么样」。 */
function Controlled({
  schema,
  initial = {},
}: {
  schema: PluginSettingsSchema;
  initial?: Record<string, unknown>;
}) {
  const [values, setValues] = useState<Record<string, unknown>>(initial);
  return (
    <>
      <SchemaForm schema={schema} values={values} onChange={setValues} />
      <output data-testid="values">{JSON.stringify(values)}</output>
    </>
  );
}

function valuesOf(): Record<string, unknown> {
  return JSON.parse(screen.getByTestId("values").textContent ?? "{}");
}

const schemaOf = (properties: PluginSettingsSchema["properties"], required?: string[]) =>
  ({ type: "object", properties, ...(required ? { required } : {}) }) as PluginSettingsSchema;

describe("SchemaForm 渲染", () => {
  it("插件没有可配置项时给出说明，而不是一片空白", () => {
    render(<SchemaForm schema={{}} values={{}} onChange={vi.fn()} />);
    expect(screen.getByText("此插件没有可配置项。")).toBeTruthy();
  });

  it("标签取 title，说明取 description", () => {
    render(
      <SchemaForm
        schema={schemaOf({
          tavern_dir: { type: "string", title: "酒馆数据目录", description: "填 default-user 那层" },
        })}
        values={{}}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByLabelText(/酒馆数据目录/)).toBeTruthy();
    expect(screen.getByText("填 default-user 那层")).toBeTruthy();
  });

  it("没写 title 时退回键名（总比空白强）", () => {
    render(
      <SchemaForm schema={schemaOf({ endpoint: { type: "string" } })} values={{}} onChange={vi.fn()} />,
    );
    expect(screen.getByLabelText("endpoint")).toBeTruthy();
  });

  it("enum 渲染下拉框，候选项来自 schema", () => {
    render(
      <Controlled
        schema={schemaOf({ mode: { type: "string", title: "模式", enum: ["local", "cloud"] } })}
      />,
    );

    const select = screen.getByLabelText("模式") as HTMLSelectElement;
    expect(select.tagName).toBe("SELECT");
    expect(screen.getByRole("option", { name: "local" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "cloud" })).toBeTruthy();
  });

  it("boolean 渲染复选框，勾选后写入 true", () => {
    render(<Controlled schema={schemaOf({ verbose: { type: "boolean", title: "详细日志" } })} />);

    const checkbox = screen.getByLabelText(/详细日志/) as HTMLInputElement;
    expect(checkbox.type).toBe("checkbox");

    fireEvent.click(checkbox);
    expect(valuesOf()).toEqual({ verbose: true });
  });

  it("number 渲染数字输入且不回显 string", () => {
    render(
      <Controlled
        schema={schemaOf({ depth: { type: "integer", title: "深度" } })}
        initial={{ depth: 3 }}
      />,
    );

    const input = screen.getByLabelText("深度") as HTMLInputElement;
    expect(input.type).toBe("number");
    expect(input.value).toBe("3");
  });

  it("format=password 用密码框（不明文回显）", () => {
    render(
      <SchemaForm
        schema={schemaOf({ token: { type: "string", title: "令牌", format: "password" } })}
        values={{ token: "secret-value" }}
        onChange={vi.fn()}
      />,
    );

    const input = screen.getByLabelText("令牌") as HTMLInputElement;
    expect(input.type).toBe("password");
  });

  it("string[] 用「每行一项」的文本域，输入转成数组", () => {
    render(
      <Controlled
        schema={schemaOf({
          hosts: { type: "array", title: "主机", items: { type: "string" } },
        })}
        initial={{ hosts: ["a.example", "b.example"] }}
      />,
    );

    const textarea = screen.getByLabelText("主机") as HTMLTextAreaElement;
    expect(textarea.value).toBe("a.example\nb.example");

    fireEvent.change(textarea, { target: { value: "x.example\n\ny.example\n" } });
    // 空行（首尾与中间）被剔除，不会变成空字符串项
    expect(valuesOf()).toEqual({ hosts: ["x.example", "y.example"] });
  });
});

describe("SchemaForm 降级策略", () => {
  it("object 字段降级为只读 JSON，并标明不支持编辑", () => {
    const nested = { retry: 3, backoff: "2s" };
    render(
      <SchemaForm
        schema={schemaOf({ policy: { type: "object", title: "重试策略" } })}
        values={{ policy: nested }}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByText(/重试策略（暂不支持编辑）/)).toBeTruthy();
    // 内容原样可见——不静默丢弃
    expect(screen.getByText(/"retry": 3/)).toBeTruthy();
    // 且不提供可编辑控件（避免「能改但存不进去」的假象）
    expect(screen.queryByLabelText("重试策略")).toBeNull();
  });

  it("没声明 type 的字段同样降级（不猜语义）", () => {
    render(
      <SchemaForm
        schema={schemaOf({ mystery: { title: "神秘项" } })}
        values={{}}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByText(/神秘项（暂不支持编辑）/)).toBeTruthy();
    expect(screen.getByText("（未设置）")).toBeTruthy();
  });

  it("array 套非 string 也降级", () => {
    render(
      <SchemaForm
        schema={schemaOf({
          matrix: { type: "array", title: "矩阵", items: { type: "number" } },
        })}
        values={{ matrix: [1, 2] }}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByText(/矩阵（暂不支持编辑）/)).toBeTruthy();
  });
});

describe("SchemaForm 值语义", () => {
  it("清空文本字段会删掉该键，而不是留一个空串", () => {
    render(
      <Controlled
        schema={schemaOf({ tavern_dir: { type: "string", title: "目录" } })}
        initial={{ tavern_dir: "/data/tavern" }}
      />,
    );

    fireEvent.change(screen.getByLabelText("目录"), { target: { value: "" } });
    expect(valuesOf()).toEqual({});
  });

  it("清空列表字段同样删键", () => {
    render(
      <Controlled
        schema={schemaOf({ hosts: { type: "array", title: "主机", items: { type: "string" } } })}
        initial={{ hosts: ["a"] }}
      />,
    );

    fireEvent.change(screen.getByLabelText("主机"), { target: { value: "" } });
    expect(valuesOf()).toEqual({});
  });

  it("保留其它字段——改一个不会顺手抹掉另一个", () => {
    render(
      <Controlled
        schema={schemaOf({
          a: { type: "string", title: "甲" },
          b: { type: "string", title: "乙" },
        })}
        initial={{ a: "1", b: "2" }}
      />,
    );

    fireEvent.change(screen.getByLabelText("甲"), { target: { value: "9" } });
    expect(valuesOf()).toEqual({ a: "9", b: "2" });
  });

  it("disabled 时控件不可编辑", () => {
    render(
      <SchemaForm
        schema={schemaOf({ a: { type: "string", title: "甲" } })}
        values={{}}
        onChange={vi.fn()}
        disabled
      />,
    );

    expect((screen.getByLabelText("甲") as HTMLInputElement).disabled).toBe(true);
  });
});

describe("必填校验", () => {
  it("必填字段打星号，缺失时给出 alert", () => {
    render(
      <SchemaForm
        schema={schemaOf({ dir: { type: "string", title: "目录" } }, ["dir"])}
        values={{}}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByTitle("必填")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("此项为必填");
  });

  it("填上值后提示消失", () => {
    render(
      <SchemaForm
        schema={schemaOf({ dir: { type: "string", title: "目录" } }, ["dir"])}
        values={{ dir: "/x" }}
        onChange={vi.fn()}
      />,
    );

    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("missingRequired：空串与空数组都算缺失（required 要的是值，不是键）", () => {
    const schema = schemaOf(
      { a: { type: "string" }, b: { type: "array", items: { type: "string" } }, c: { type: "string" } },
      ["a", "b", "c"],
    );

    expect(missingRequired(schema, { a: "   ", b: [], c: "有值" })).toEqual(["a", "b"]);
    expect(missingRequired(schema, { a: "x", b: ["y"], c: "z" })).toEqual([]);
  });

  it("数字字段的 0 与布尔 false 不算缺失", () => {
    const schema = schemaOf({ n: { type: "integer" }, f: { type: "boolean" } }, ["n", "f"]);
    expect(missingRequired(schema, { n: 0, f: false })).toEqual([]);
  });
});
