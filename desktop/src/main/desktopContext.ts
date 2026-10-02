/**
 * 桌面情景采集（主进程）。
 *
 * 设计见 `docs/proactive-multimodal.md` §4.2 / §4.4。**主进程只采集，不判断、不联网、
 * 不接触密钥**——采集结果经 IPC 交给渲染层，由渲染层直连后端上报
 * （`docs/desktop-pet.md` §4 的既有边界）。
 *
 * ## 为什么用 PowerShell 而不是新增 npm 依赖
 *
 * 「正在听什么歌」（Windows SMTC）与「前台窗口是谁」都需要原生 WinRT / Win32 能力，
 * Electron 本身不暴露。可选路径有三条：
 *
 * | 方案 | 代价 |
 * | --- | --- |
 * | `active-win` / `node-smtc` 等 npm 包 | 新增依赖，且多数只覆盖其中一项 |
 * | 自己写原生插件（node-gyp） | 要维护编译链，发布包复杂度大增 |
 * | **PowerShell 调用（本实现）** | 零依赖、零编译；代价是每次约 100–300ms |
 *
 * 按 `AGENTS.md §5`「新增第三方依赖前先列出方案」的口径，这里选第三条：
 * 采集是**低频**行为（默认 30 秒一次），几百毫秒的开销可以接受，
 * 而少一个原生依赖意味着安装包不会因为 ABI 不匹配在别人机器上起不来。
 *
 * ## 两条硬约束
 *
 * 1. **脚本以 `-EncodedCommand` 内联传入**（base64 / UTF-16LE），不落 `.ps1` 文件。
 *    落盘脚本在非 ASCII 路径下会有编码问题（中文用户名），而内联完全绕开它；
 *    顺带也不需要在发布包里额外带一个脚本资源。
 * 2. **任何失败都降级为「拿不到」**，绝不抛错。采集不到桌面情景只是少了主动开口的
 *    由头，不该让桌宠窗起不来。
 */

import { execFile } from "node:child_process";
import { powerMonitor } from "electron";

import type { DesktopContextSnapshot } from "../shared/ipc";
import { EMPTY_DESKTOP_CONTEXT, normalizeDesktopContext } from "../shared/ipc";

/** 采集缓存时长（毫秒）：渲染层可能连续问两次，没必要重复起进程 */
const CACHE_TTL_MS = 10_000;

/** PowerShell 超时（毫秒）。超时即放弃本次采集（下一次再试） */
const SCRIPT_TIMEOUT_MS = 5_000;

/**
 * 采集脚本（**纯 ASCII**，避免任何编码问题）。
 *
 * 输出一行 JSON；任一段失败时对应的字段为空（调用方按「拿不到」处理）。
 * `SMTC` 那段包在 try 里：Windows 10 早期版本与 Server 版没有这个 WinRT 类，
 * 而「放不出歌名」不该让整个采集失败——前台窗口那部分仍然有效。
 */
const COLLECT_SCRIPT = `
$ErrorActionPreference = 'SilentlyContinue'
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class HyPrForeground {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
  [DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr hWnd, out int processId);
}
"@
$result = @{}
$handle = [HyPrForeground]::GetForegroundWindow()
if ($handle -ne [IntPtr]::Zero) {
  $buffer = New-Object System.Text.StringBuilder 512
  [void][HyPrForeground]::GetWindowText($handle, $buffer, 512)
  $result.foreground_title = $buffer.ToString()
  $processId = 0
  [void][HyPrForeground]::GetWindowThreadProcessId($handle, [ref]$processId)
  if ($processId -gt 0) {
    $proc = Get-Process -Id $processId
    if ($proc) { $result.foreground_process = $proc.ProcessName + '.exe' }
  }
}
try {
  [void][Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
  $manager = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync().GetAwaiter().GetResult()
  $session = $manager.GetCurrentSession()
  if ($session) {
    $props = $session.TryGetMediaPropertiesAsync().GetAwaiter().GetResult()
    if ($props) {
      $result.now_playing_title = $props.Title
      $result.now_playing_artist = $props.Artist
    }
  }
} catch { }
$result | ConvertTo-Json -Compress
`;

/** 把脚本编成 PowerShell 的 `-EncodedCommand` 参数（base64 / UTF-16LE） */
function encodeCommand(script: string): string {
  return Buffer.from(script, "utf16le").toString("base64");
}

const ENCODED_SCRIPT = encodeCommand(COLLECT_SCRIPT);

let cache: { at: number; value: DesktopContextSnapshot } | null = null;

function localTimeParts(now: Date): { local_time: string; local_date: string } {
  const pad = (value: number) => String(value).padStart(2, "0");
  return {
    local_time: `${pad(now.getHours())}:${pad(now.getMinutes())}`,
    local_date: `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`,
  };
}

/**
 * 只读「本机时间 + 空闲秒数」的那部分。
 *
 * 这两个不依赖 PowerShell，任何平台都能拿到；因此即使下面的脚本整段失败，
 * 「现在是几点」「人还在不在」仍然有效——而深夜关怀只靠这两项就能工作。
 *
 * 从 `EMPTY_DESKTOP_CONTEXT` 展开而不是只写两个字段：返回值类型因此始终完整，
 * 调用方不必处理「字段可能不存在」——那正是跨 IPC 后变成 422 的源头。
 */
function localOnlySnapshot(): DesktopContextSnapshot {
  let idleSeconds = 0;
  try {
    // 必须在 app ready 之后调用；未 ready 时 getSystemIdleTime 会抛
    idleSeconds = powerMonitor.getSystemIdleTime();
  } catch {
    idleSeconds = 0;
  }
  return {
    ...EMPTY_DESKTOP_CONTEXT,
    idle_seconds: idleSeconds,
    ...localTimeParts(new Date()),
  };
}

function runCollectScript(): Promise<Partial<DesktopContextSnapshot>> {
  return new Promise((resolve) => {
    execFile(
      "powershell.exe",
      ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", ENCODED_SCRIPT],
      { timeout: SCRIPT_TIMEOUT_MS, windowsHide: true, maxBuffer: 1024 * 1024 },
      (error, stdout) => {
        if (error || !stdout) {
          resolve({});
          return;
        }
        const text = stdout.trim();
        if (!text) {
          resolve({});
          return;
        }
        try {
          const parsed = JSON.parse(text) as Record<string, unknown>;
          const pick = (key: string): string =>
            typeof parsed[key] === "string" ? (parsed[key] as string).trim() : "";
          resolve({
            foreground_title: pick("foreground_title"),
            foreground_process: pick("foreground_process"),
            now_playing_title: pick("now_playing_title"),
            now_playing_artist: pick("now_playing_artist"),
          });
        } catch {
          // 输出不是 JSON（PowerShell 打了别的东西）→ 当作拿不到，不抛
          resolve({});
        }
      },
    );
  });
}

/**
 * 采集一次桌面情景。
 *
 * `force = true` 时绕过缓存（设置面板的「立即刷新」用）。
 */
export async function collectDesktopContext(
  force = false,
): Promise<DesktopContextSnapshot> {
  const now = Date.now();
  if (!force && cache && now - cache.at < CACHE_TTL_MS) {
    return cache.value;
  }

  const base = localOnlySnapshot();
  // 非 Windows 平台没有 SMTC / GetForegroundWindow：只回时间与空闲，
  // 主动链路的「深夜关怀」照样可用（见 localOnlySnapshot 的说明）。
  const extra =
    process.platform === "win32" ? await runCollectScript() : ({} as Partial<DesktopContextSnapshot>);

  // 归一化后再出主进程：脚本输出是**外部数据**（JSON 解析结果），
  // 且要跨 IPC 传进渲染层。字段缺失会让后端的 pydantic 校验直接 422，
  // 表现为「上报静默失败」——那是最难查的一类。这里补齐即可。
  const value = normalizeDesktopContext({ ...base, ...extra });
  cache = { at: now, value };
  return value;
}

/** 清掉缓存（桌宠窗关闭 / 设置变更时调用，避免下次读到过期数据） */
export function resetDesktopContextCache(): void {
  cache = null;
}
