# 随包分发的第三方组件许可证

本目录存放**随发布包一起分发**的第三方组件的许可证文本。安装包会把整个目录
拷到 `resources/backend-res/THIRD_PARTY_LICENSES/`（见 `desktop/package.json`
的 `extraResources`），履行各自的再分发义务。

## 为什么需要随包分发

HyPRA 自身以 **AGPL-3.0** 发布（见根目录 `LICENSE`），但发布包里打进了两个
**独立程序**，它们各自的许可证与 AGPL 无关：

| 组件 | 版本 | 许可证 | 为什么在包里 | 义务 |
| --- | --- | --- | --- | --- |
| Qdrant | 1.19.1 | **Apache-2.0** | 温层向量库；评审要「一键启动」就必须自带 | 保留许可证与版权声明（Apache-2.0 §4(a)(c)） |
| PyInstaller 运行时（`backend.exe` 内含） | — | **GPL-2.0-or-later + 启动器例外** | 把后端打包成免装 Python 的 exe | 自建 spec 属「构建产物」，不受 GPL 传染；PyInstaller 的例外条款明确允许打包专有/其他许可程序。若分发，保留其许可证即可 |

> 说明：Qdrant 是 Apache-2.0（**非** AGPL），因此把它的二进制放进发布包
> 不会改变 HyPRA 自身的协议；反过来，HyPRA 的 AGPL 也不会「传染」到 Qdrant
> 这个独立程序。两者的边界是**进程 + 文件**边界，不是代码级链接。

## 文件清单

- `qdrant-LICENSE.txt` —— Qdrant 的 Apache-2.0 许可证全文。
  来源：<https://github.com/qdrant/qdrant/blob/v1.19.1/LICENSE>（与
  <https://www.apache.org/licenses/LICENSE-2.0.txt> 相同）。

## 维护约定

- 升级 Qdrant 版本时，**重新核对其 LICENSE**（许可证若变更，需更新本目录并
  同步 `docs/license-compliance.md`）。
- 新增任何随包分发的第三方二进制/资源时，必须在此登记，否则视为违规
  （见 `AGENTS.md` §6「代码来源纪律」）。
