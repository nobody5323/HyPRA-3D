"use client";

/**
 * 「还能用哪些模型」：模型来源列表（`AGENTS.md §9.10` 第 15 项的前端面）。
 *
 * 与上面的「模型库」分工：那是**已装**的（可直接选用），这里回答
 * 「还有哪些可以获取」——所以它只读、不参与选用。
 *
 * **合规上必须显示作者与授权**：模型不随本项目分发，用户得知道来源与条款才能
 * 判断能不能用（§6 红线）。因此这里**没有「安装」按钮**，只有一个指向来源页的链接；
 * 授权未标注时明确写「未标注」而不是留空——空白会让人以为没有条款限制。
 */

import { useEffect, useState } from "react";

import { ApiError, listAvatarModelSources } from "@/lib/api/client";
import type { AvatarModelSources } from "@/lib/api/types";

const HINT_CLASS = "mt-1 text-[11px] leading-relaxed text-ink-faint";

function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : fallback;
}

export function ModelSourcePanel() {
  const [catalog, setCatalog] = useState<AvatarModelSources | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    listAvatarModelSources()
      .then((next) => {
        if (cancelled) return;
        setCatalog(next);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(errorText(err, "读取模型来源失败"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const available = (catalog?.models ?? []).filter((model) => !model.installed);
  const manifest = catalog?.sources.find((source) => source.id === "remote-manifest");

  return (
    <div className="mt-4 border-t border-line pt-3">
      <h4 className="text-[11px] font-medium text-ink-muted">可获取的模型</h4>

      {loading && <p className={HINT_CLASS}>读取中…</p>}

      {!loading && error && (
        <p role="alert" className="mt-1 text-[11px] text-danger-text">
          {error}
        </p>
      )}

      {!loading && !error && (
        <>
          {!manifest?.available ? (
            <p className={HINT_CLASS}>
              还没配置模型清单。在「能力中心 → 数字人模型来源」里填一个清单文件路径，
              这里就会列出可获取的模型（含作者与授权）。清单格式见插件目录下的{" "}
              <code className="font-mono">model-manifest.example.json</code>。
            </p>
          ) : available.length === 0 ? (
            <p className={HINT_CLASS}>
              清单里没有可获取的模型（本机已有的不会重复列出）。
            </p>
          ) : (
            <ul className="mt-1.5 flex flex-col gap-1.5">
              {available.map((model) => (
                <li
                  key={`${model.source}:${model.id}`}
                  className="rounded-lg border border-line px-2.5 py-2 text-[11px]"
                >
                  <p className="font-medium text-ink">{model.displayName}</p>
                  {model.description && (
                    <p className="mt-0.5 text-ink-soft">{model.description}</p>
                  )}
                  <p className="mt-0.5 text-ink-faint">
                    {model.author && <>作者：{model.author} · </>}
                    授权：{model.license || "未标注（使用前请自行确认）"}
                  </p>
                  {model.homepage ? (
                    <a
                      href={model.homepage}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="focus-ring mt-1 inline-block text-accent-text underline-offset-2 hover:underline"
                    >
                      到来源页获取 →
                    </a>
                  ) : (
                    <p className={HINT_CLASS}>清单未给出获取地址。</p>
                  )}
                </li>
              ))}
            </ul>
          )}

          <p className={HINT_CLASS}>
            模型不随本项目分发。请到来源页自行获取后，用上面的「模型库」上传使用。
          </p>
        </>
      )}
    </div>
  );
}
