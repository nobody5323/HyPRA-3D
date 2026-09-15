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
      },
      animation: {
        "breathe-in": "breathe 4s ease-in-out infinite",
        "sound-wave": "wave 1.2s ease-in-out infinite",
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
      },
    },
  },
  plugins: [],
};

export default config;
