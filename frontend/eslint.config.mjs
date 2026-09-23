import { dirname } from "path";
import { fileURLToPath } from "url";

import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({ baseDirectory: __dirname });

/**
 * ESLint 扁平配置（ESLint 9）。
 *
 * 规则集用 Next 官方 `next/core-web-vitals`，其中与本项目最相关的两类：
 * - `react-hooks/*`：useEffect 依赖数组（`useAvatar.ts` 里有多处刻意豁免，
 *   必须在 CI 里保住 `eslint-disable-next-line` 的有效性）；
 * - `jsx-a11y/*`：可访问性（项目已按 summaries/audit-frontend 的结论做过改造）。
 */
const eslintConfig = [
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "next-env.d.ts", // Next 自动生成，不参与 lint
      "eslint-report.json",
      "vendor/**", // 第三方 SDK 源码（官方 Framework，自带的 eslint 指令不属于我们的规则集）
      "public/**", // 静态资源（含官方 Core 的压缩 js）
    ],
  },
  ...compat.extends("next/core-web-vitals"),
];

export default eslintConfig;
