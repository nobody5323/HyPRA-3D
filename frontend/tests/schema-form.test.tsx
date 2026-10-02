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

import { SchemaForm, missingRequired, outOfRange } from "@/components/settings/SchemaForm";
import type { PluginSettingsSchema } from "@/lib/api/types";

afterEach(cleanup);

/** 受控包装：把内部值吐到 DOM，便于断言「改完之后 values 到底长什么样」。 */
function Controlled({
  schema,
  initial = {},
  secretsSet,
}: {
  schema: PluginSettingsSchema;
  initial?: Record<string, unknown>;
  secretsSet?: string[];
}) {
  const [values, setValues] = useState<Record<string, unknown>>(initial);
  return (
    <>
      <SchemaForm schema={schema} values={values} onChange={setValues} secretsSet={secretsSet} />
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

  it("已配置的密钥字段提示「已保存，留空则沿用」", () => {
    render(
      <SchemaForm
        schema={schemaOf({ token: { type: "string", title: "令牌", format: "password" } })}
        values={{}}
        onChange={vi.fn()}
        secretsSet={["token"]}
      />,
    );

    const input = screen.getByLabelText("令牌") as HTMLInputElement;
    expect(input.value).toBe(""); // 后端不下发明文，表单不回填
    expect(input.placeholder).toBe("已保存，留空则沿用");
  });

  it("未配置的密钥字段不显示「已保存」与清除按钮", () => {
    render(
      <SchemaForm
        schema={schemaOf({ token: { type: "string", title: "令牌", format: "password" } })}
        values={{}}
        onChange={vi.fn()}
        secretsSet={[]}
      />,
    );

    const input = screen.getByLabelText("令牌") as HTMLInputElement;
    expect(input.placeholder).toBe("");
    expect(screen.queryByRole("button", { name: "清除已保存" })).toBeNull();
  });

  it("非密钥字段不给清除按钮（留着会是死按钮）", () => {
    render(
      <SchemaForm
        schema={schemaOf({ tavern_dir: { type: "string", title: "酒馆数据目录" } })}
        values={{ tavern_dir: "D:/ST" }}
        onChange={vi.fn()}
        secretsSet={["tavern_dir"]}
      />,
    );

    expect(screen.queryByRole("button", { name: "清除已保存" })).toBeNull();
  });

  it("「清除已保存」把该字段标成 null，其它字段不受影响", () => {
    render(
      <Controlled
        schema={schemaOf({
          token: { type: "string", title: "令牌", format: "password" },
          keep: { type: "string", title: "保留" },
        })}
        initial={{ keep: "x" }}
        secretsSet={["token"]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "清除已保存" }));

    expect(valuesOf()).toEqual({ keep: "x", token: null });
    // 点完按钮后不再提示「已保存」（用户已表达了清除意图）
    expect(screen.queryByRole("button", { name: "清除已保存" })).toBeNull();
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

  it("密钥字段的显式 null 不算缺失（那是「清除」，不是「没填」）", () => {
    // 否则会卡成「点清除 → 保存被拦 → 清除按钮已消失」的死路
    const schema = schemaOf(
      { token: { type: "string", format: "password" }, other: { type: "string" } },
      ["token", "other"],
    );

    expect(missingRequired(schema, { token: null, other: "x" })).toEqual([]);
    // 非密钥字段的 null 仍算缺失（那是真的没值）
    expect(missingRequired(schema, { token: "t", other: null })).toEqual(["other"]);
  });
});

describe("数值范围校验", () => {
  const numericSchema = schemaOf({
    dim: { type: "integer", title: "维度", minimum: 1 },
    speed: { type: "number", title: "语速", minimum: 0.1, maximum: 3 },
    model: { type: "string", title: "模型" },
  });

  it("最小/最大值越界会被拦下", () => {
    expect(outOfRange(numericSchema, { dim: 0 })).toEqual(["dim"]);
    expect(outOfRange(numericSchema, { speed: "9" })).toEqual(["speed"]);
    expect(outOfRange(numericSchema, { dim: 1024, speed: 0.1 })).toEqual([]);
  });

  it("非数值也拦（后端会静默忽略，界面却不能装作填上了）", () => {
    expect(outOfRange(numericSchema, { dim: "很大" })).toEqual(["dim"]);
  });

  it("未填的数值字段不参与校验，非数值字段不看范围", () => {
    expect(outOfRange(numericSchema, { dim: "", model: "随便写的" })).toEqual([]);
    expect(outOfRange(numericSchema, {})).toEqual([]);
  });
});
