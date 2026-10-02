import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // ---- 浅色主题语义色板（温和米白纸感 + 鼠尾草绿点缀）----
        // 面：页面根底 / 卡片浮起 / 抬起层 / 内嵌输入 / 悬停
        surface: {
          base: "#FAF7F2",
          panel: "#FFFFFF",
          raised: "#F5F1EA",
          inset: "#F1EDE6",
          hover: "#EDE8E0",
        },
        // 数字人画布：保持深色不透明（SDK 是否自绘背景不可知，浅底有风险）
        stage: { canvas: "#2B2723" },
        // 文字阶梯（surface-base 上的实测对比度：ink 13.3:1 / muted 6.9:1 / soft 4.8:1 / faint 3.1:1）
        ink: {
          DEFAULT: "#2F2A26",
          muted: "#5C554D",
          soft: "#766D63",
          faint: "#948B80",
          on: "#FFFFFF",
        },
        // 描边与分隔线
        line: { DEFAULT: "#E7E1D8", strong: "#D6CFC4" },
        // 强调色（鼠尾草绿）：DEFAULT 用作**实底**（主按钮/用户气泡），白字实测 5.03:1 ✓
        accent: {
          DEFAULT: "#4E776B",
          hover: "#3F6357",
          soft: "#EAF1EE",
          text: "#3F6357",
        },
        // 状态色（成功 / 危险 / 警告）
        success: { DEFAULT: "#5B9A73", soft: "#EAF4EE", text: "#3C6B4E" },
        danger: { DEFAULT: "#A85B55", soft: "#FBEFEE", text: "#8F4A45" },
        warning: { DEFAULT: "#C89A5B", soft: "#FBF3E7", text: "#8A6532" },
        // 服务商品牌色（魔珐 SDK 徽标）
        brand: { DEFAULT: "#8B7BB0", soft: "#F1EEF7", text: "#5C4E7D" },
        // 情绪主题色（唯一来源：MoodIndicator 强度条 + AvatarStage 光晕/发光）
        mood: {
          happy: "#E0A24E",
          calm: "#6E9BB5",
          sad: "#7E8FA8",
          anxious: "#D08B5E",
          tired: "#8B87A8",
          angry: "#C4706A",
          surprised: "#D4A94F",
          neutral: "#9A948C",
        },
      },
      fontFamily: {
        // 中文优先字体栈（零依赖：不引入 webfont，中文字形由系统提供）
        sans: [
          '"PingFang SC"',
          '"Microsoft YaHei"',
          '"Noto Sans SC"',
          "system-ui",
          "-apple-system",
          '"Segoe UI"',
          "sans-serif",
        ],
        mono: ['"Cascadia Code"', '"SFMono-Regular"', "Consolas", '"Liberation Mono"', "monospace"],
      },
      /**
       * 阴影阶梯（浅色主题专用：暖灰投影而非纯黑）。
       *
       * 三层各司其职，**不要在组件里手写 shadow-[...]**：
       * - `card`：普通卡片，几乎只是一层极淡的边影，靠描边立住；
       * - `panel`：浮起的面板（弹出层 / 抽屉 / 舞台）；
       * - `float`：最高层（模态、下拉）。
       * 颜色带一点暖调（与 surface 的米白同族），纯黑投影在暖底上会显脏。
       */
      boxShadow: {
        card: "0 1px 2px rgba(58, 48, 38, 0.04), 0 6px 20px -14px rgba(58, 48, 38, 0.18)",
        panel: "0 2px 6px rgba(58, 48, 38, 0.05), 0 18px 44px -24px rgba(58, 48, 38, 0.24)",
        float: "0 8px 20px rgba(58, 48, 38, 0.10), 0 32px 72px -32px rgba(58, 48, 38, 0.32)",
        // 内高光：给实底按钮/深色画布加一道顶部提亮，避免「一片死色」
        highlight: "inset 0 1px 0 rgba(255, 255, 255, 0.18)",
      },
      animation: {
        "breathe-in": "breathe 4s ease-in-out infinite",
        "sound-wave": "wave 1.2s ease-in-out infinite",
        // 面板/列表项入场：轻微上浮，克制（120ms 级别的位移，不喧宾夺主）
        "fade-up": "fade-up 260ms cubic-bezier(0.22, 1, 0.36, 1) both",
        // 状态点呼吸：表示「活着」（后端在线、服务运行中）
        "pulse-dot": "pulse-dot 2.4s ease-in-out infinite",
      },
      keyframes: {
        breathe: {
          "0%, 100%": { opacity: "0.55", transform: "scale(1)" },
          "50%": { opacity: "0.9", transform: "scale(1.03)" },
        },
        wave: {
          "0%, 100%": { transform: "scaleY(0.4)" },
          "50%": { transform: "scaleY(1)" },
        },
        "fade-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "pulse-dot": {
          "0%, 100%": { opacity: "1", transform: "scale(1)" },
          "50%": { opacity: "0.45", transform: "scale(0.82)" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
