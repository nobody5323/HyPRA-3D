"use client";

/**
 * JSON Schema 驱动的插件配置表单（`AGENTS.md §9.5`）。
 *
 * 插件不写 React 组件，只给 `settings_schema`，由宿主渲染——避开运行时加载
 * 前端代码的全部代价（React 单例、版本契约、CSS 隔离）。这是 §9.7「不做插件 UI」
 * 的替代方案，不是权宜之计。
 *
 * 只渲染**用得上的子集**：enum / boolean / number / string / string[]。
 * 遇到渲染不了的类型（object、没声明 type、array 套非 string）**不猜也不丢**：
 * 降级成只读 JSON，用户至少看得到内容，也知道得手工改配置文件。
 * 这比「渲染成空输入框、保存时把字段抹掉」安全得多——后者会静默毁掉配置。
 *
 * 受控组件：值全在 `values` 里，不做内部副本。两处约定由调用方负责：
 * ① 加载时把 schema 的 `default` 合并进 values（否则界面看着是空、实际是默认值）；
 * ② 保存前用 `missingRequired()` 检查必填项。
 *
 * 密钥字段（`format: "password"`）例外：后端**永不下发明文**，因此表单不回填，
 * 靠 `secretsSet` 把提示说准（「已保存，留空则沿用」），并给一个显式的
 * 「清除已保存」——留空只等于沿用，没有这个按钮用户就没法回落到 `.env`。
 */

import { useId } from "react";

import type { PluginSettingField, PluginSettingsSchema } from "@/lib/api/types";

const FIELD_CLASS =
  "focus-ring mt-1 w-full rounded-lg border border-line bg-surface-inset px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-accent";

const LABEL_CLASS = "text-[11px] font-medium text-ink-muted";
const HINT_CLASS = "mt-1 text-[11px] leading-relaxed text-ink-faint";

/** 渲染方式；`unsupported` 表示该字段的类型声明超出了表单能力。 */
type FieldKind =
  | "enum"
  | "boolean"
  | "number"
  | "text"
  | "password"
  | "string-list"
  | "unsupported";

function fieldKind(field: PluginSettingField): FieldKind {
  // enum 优先于 type：候选项是比类型更强的约束，渲染成下拉框不会让用户填出非法值
  if (Array.isArray(field.enum) && field.enum.length > 0) return "enum";
  switch (field.type) {
    case "boolean":
      return "boolean";
    case "number":
    case "integer":
      return "number";
    case "string":
      return field.format === "password" ? "password" : "text";
    case "array":
      return field.items?.type === "string" ? "string-list" : "unsupported";
    default:
      // 含 undefined（没声明类型）与 object：不猜语义
      return "unsupported";
  }
}

/** 值转文本（只读展示与文本输入共用）。 */
function asText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function asList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/**
 * 写入/清除一个键。
 *
 * 空值（""、null、undefined、空数组）一律**删除键**而不是写入空串：对路径这类字段
 * 来说「清空」的语义是「不配置」，留个 `""` 会让后端把它当成一个真实的空路径。
 *
 * 例外是密钥字段的「清除」（见 `withClearedSecret`）：那里的 `null` 是一个**有意义的
 * 指令**，不能与「没填」混为一谈。
 */
function withValue(
  values: Record<string, unknown>,
  key: string,
  value: unknown,
): Record<string, unknown> {
  const next = { ...values };
  const empty =
    value === "" || value === null || value === undefined || (Array.isArray(value) && value.length === 0);
  if (empty) delete next[key];
  else next[key] = value;
  return next;
}

/**
 * 标记「清除已保存的密钥」。
 *
 * 刻意写 `null` 而不是删键：后端把「提交里没有这个键」解释为**沿用**，
 * 把 `null` 解释为**清除**（回落 .env）。两者必须分得开，否则用户没有退路。
 */
function withClearedSecret(
  values: Record<string, unknown>,
  key: string,
): Record<string, unknown> {
  return { ...values, [key]: null };
}

/** 取字段的展示名（没写 title 时退回键名，总比空白强）。 */
function labelOf(key: string, field: PluginSettingField): string {
  return field.title?.trim() || key;
}

/**
 * 必填但缺失的字段名（保存前调用）。
 *
 * 空串与空数组都算缺失——schema 的 `required` 表达的是「必须给出一个值」，
 * 而不是「键必须存在」。
 */
export function missingRequired(
  schema: PluginSettingsSchema,
  values: Record<string, unknown>,
): string[] {
  return (schema.required ?? []).filter((key) => {
    const value = values[key];
    // 密钥字段的显式 `null` 是「清除已保存」这个**指令**，不是「没填」：
    // 若算作缺失，就会出现「点清除 → 保存被拦 → 按钮已消失」的死路
    // （`required` + `password` 是罕见但合法的组合）。
    if (value === null && schema.properties?.[key]?.format === "password") return false;
    if (value === undefined || value === null) return true;
    if (typeof value === "string") return value.trim() === "";
    if (Array.isArray(value)) return value.length === 0;
    return false;
  });
}

/**
 * 越界的数值字段（保存前调用）。
 *
 * 为什么不靠浏览器原生校验：这些控件不在 `<form>` 里，`min` / `max` 属性不会触发
 * 提交拦截；后端又**刻意不校验 schema**（见 `PluginDetail` 的说明）。而数值越界在这类
 * 配置里后果很实在：`embedding_dim=0` 会一路进到建向量库时才炸，报错还看不出根因。
 *
 * 非数值（输入框里手打进去的乱字符）同样拦住：后端会静默忽略它（回落 .env），
 * 界面上却看起来「填了」，那种不一致比直接报错更难查。
 */
export function outOfRange(
  schema: PluginSettingsSchema,
  values: Record<string, unknown>,
): string[] {
  return Object.entries(schema.properties ?? {})
    .filter(([key, field]) => {
      if (field.type !== "number" && field.type !== "integer") return false;
      const raw = values[key];
      if (raw === undefined || raw === null || raw === "") return false;

      const value = Number(raw);
      if (Number.isNaN(value)) return true;
      if (field.minimum !== undefined && value < field.minimum) return true;
      if (field.maximum !== undefined && value > field.maximum) return true;
      return false;
    })
    .map(([key]) => key);
}

export function SchemaForm({
  schema,
  values,
  onChange,
  disabled = false,
  idPrefix,
  secretsSet,
}: {
  schema: PluginSettingsSchema;
  values: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  disabled?: boolean;
  /** id 前缀；多实例同页时避免 label 串到别人的输入框 */
  idPrefix?: string;
  /**
   * 后端标记为「已配置」的密钥字段名（`GET /plugins/{id}/settings` 的 `secrets_set`）。
   *
   * 密钥明文永不下发，表单因此拿不到回填值；有这一份名单才能把提示说准：
   * 「已保存，留空则沿用」与「还没配」对用户是两件事。
   */
  secretsSet?: string[];
}) {
  const generatedId = useId();
  const base = `${idPrefix ?? "plugin-setting"}-${generatedId}`;
  const properties = schema.properties ?? {};
  const required = new Set(schema.required ?? []);
  const savedSecrets = new Set(secretsSet ?? []);
  const keys = Object.keys(properties);

  if (keys.length === 0) {
    return <p className={HINT_CLASS}>此插件没有可配置项。</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      {keys.map((key) => {
        const field = properties[key];
        const kind = fieldKind(field);
        const id = `${base}-${key}`;
        const hintId = `${id}-hint`;
        const value = values[key];
        const isRequired = required.has(key);
        const missing = isRequired && missingRequired(schema, values).includes(key);
        const isSecret = kind === "password";
        // `null` = 用户已点过「清除已保存」（见 withClearedSecret）：这时不该再提示「已保存」
        const secretSaved = isSecret && savedSecrets.has(key) && value !== null;

        const label = (
          <label htmlFor={id} className={LABEL_CLASS}>
            {labelOf(key, field)}
            {isRequired && (
              <span className="ml-0.5 text-danger-text" title="必填">
                *
              </span>
            )}
          </label>
        );

        const hint = field.description ? (
          <p id={hintId} className={HINT_CLASS}>
            {field.description}
          </p>
        ) : null;

        // 不支持的字段：只读展示，不渲染可编辑控件——避免"能改但存不进去"的假象
        if (kind === "unsupported") {
          return (
            <div key={key} className="flex flex-col">
              <span className={LABEL_CLASS}>{labelOf(key, field)}（暂不支持编辑）</span>
              {hint}
              <pre className="mt-1 overflow-x-auto rounded-lg border border-line bg-surface-inset px-3 py-2 text-[11px] text-ink-muted">
                {value === undefined ? "（未设置）" : JSON.stringify(value, null, 2)}
              </pre>
            </div>
          );
        }

        const describedBy = field.description ? hintId : undefined;

        return (
          <div key={key} className="flex flex-col">
            {kind === "boolean" ? (
              // checkbox 的 label 与控件并排，不用 htmlFor 竖排（点整行都能切换）
              <label className="flex items-start gap-2">
                <input
                  id={id}
                  type="checkbox"
                  className="focus-ring mt-0.5 size-4 rounded border-line accent-accent"
                  checked={Boolean(value)}
                  disabled={disabled}
                  aria-describedby={describedBy}
                  onChange={(event) => onChange(withValue(values, key, event.target.checked))}
                />
                <span className={LABEL_CLASS}>
                  {labelOf(key, field)}
                  {isRequired && (
                    <span className="ml-0.5 text-danger-text" title="必填">
                      *
                    </span>
                  )}
                </span>
              </label>
            ) : (
              label
            )}

            {kind === "enum" && (
              <select
                id={id}
                className={FIELD_CLASS}
                value={asText(value)}
                disabled={disabled}
                aria-describedby={describedBy}
                aria-invalid={missing}
                onChange={(event) => onChange(withValue(values, key, event.target.value))}
              >
                <option value="">（未设置）</option>
                {(field.enum ?? []).map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            )}

            {kind === "number" && (
              <input
                id={id}
                type="number"
                className={FIELD_CLASS}
                value={asText(value)}
                disabled={disabled}
                aria-describedby={describedBy}
                aria-invalid={missing}
                min={field.minimum}
                max={field.maximum}
                onChange={(event) => onChange(withValue(values, key, event.target.value))}
              />
            )}

            {(kind === "text" || kind === "password") && (
              <div className="flex gap-2">
                <input
                  id={id}
                  type={kind === "password" ? "password" : "text"}
                  className={FIELD_CLASS}
                  value={asText(value)}
                  disabled={disabled}
                  aria-describedby={describedBy}
                  aria-invalid={missing}
                  autoComplete={kind === "password" ? "new-password" : "off"}
                  placeholder={
                    kind === "password" && secretSaved ? "已保存，留空则沿用" : undefined
                  }
                  onChange={(event) => onChange(withValue(values, key, event.target.value))}
                />
                {/* 清除是密钥字段专属的动作：留空只等于「沿用」，没有它就没有回到 .env 的路 */}
                {secretSaved && (
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => onChange(withClearedSecret(values, key))}
                    className="focus-ring shrink-0 rounded-lg border border-line px-2.5 text-[11px] text-ink-muted transition-colors hover:bg-surface-hover disabled:opacity-50"
                  >
                    清除已保存
                  </button>
                )}
              </div>
            )}

            {kind === "string-list" && (
              <>
                <textarea
                  id={id}
                  className={`${FIELD_CLASS} min-h-[64px] font-mono`}
                  value={asList(value).join("\n")}
                  disabled={disabled}
                  aria-describedby={describedBy}
                  aria-invalid={missing}
                  placeholder="每行一项"
                  onChange={(event) =>
                    onChange(
                      withValue(
                        values,
                        key,
                        event.target.value
                          .split("\n")
                          .map((line) => line.trim())
                          .filter(Boolean),
                      ),
                    )
                  }
                />
                <p className={HINT_CLASS}>每行一项。</p>
              </>
            )}

            {hint}
            {missing && (
              <p role="alert" className="mt-1 text-[11px] text-danger-text">
                此项为必填。
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
