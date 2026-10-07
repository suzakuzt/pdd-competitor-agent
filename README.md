# 拼多多竞品店铺监测程序

本仓库保存项目自行编写的 Python 后端、Chrome 采集脚本、数据分析逻辑、网页内容组件、测试和采集 Skill。

这是**源码公开版本**，不是原本机完整运行包。不包含真实店铺数据、商品图片、登录会话、API 密钥、历史验收证据、预装解释器或第三方 Data App 界面运行时。克隆后不能直接启动与原本机相同的完整数据舱。

## 已实现的程序功能

- 添加店铺链接后，先使用普通 Google Chrome 的已有登录会话识别店铺，加入店铺目录；识别与采集是两个独立步骤。用户点击“开始采集”后才读取该店商品。
- 采集店铺上新列表中的商品、页面明示价格与销量，检查并保存商品主图，展示实际任务进度和失败原因。
- 按店铺隔离商品和任务；同一天的数据用于更新当天展示，同时保留原始采集快照和此前日期记录。
- SKU 按需单独采集：店内搜索、核对商品、打开规格窗口、读取价格与图片、关闭并返回；普通全店采集不自动遍历 SKU。
- 提供商品图片放大、销量变化排序、生日及活动筛选、商品历史和基于已采数据的简单趋势估算。
- 使用 SQLite、图片归档、校验及备份流程保存数据；支持本机任务状态查询和采集 Skill。

这些是代码实现范围，不代表任何店铺或平台页面都一定可采集。页面结构变化、登录失效、平台验证或身份不一致会导致任务暂停。趋势估算不是销量、收益或准确率保证。

## 代码结构

| 目录或文件 | 用途 |
|---|---|
| `pdd_monitor/` | 数据库、店铺目录、采集服务、接入状态、数据分析和本机 HTTP 服务 |
| `scripts/` | Chrome 连接、商品与 SKU 读取、图片处理、备份和发布工具 |
| `dashboard/src/content/` | 自行编写的 React 内容组件、样式和展示模型 |
| `dashboard/tests/` | 展示模型及组件行为测试 |
| `tests/` | Python 后端和 Node 采集流程测试，以临时合成数据为主 |
| `skills/pdd-collect/` | 专用店铺采集 Skill 与调用桥接 |
| `schema.sql`、`queries.sql` | 数据结构和查询定义 |
| `pyproject.toml` | Python 包元数据 |

## 开发环境与依赖

Python 包要求 Python 3.10 或更新版本；建议使用 Python 3.12。Node 脚本和测试建议使用 Node.js 22 或更新版本。浏览器采集使用 Google Chrome，部分凭据管理和交付工具针对 Windows。

基础 Python 模块主要使用标准库，可在自行创建的虚拟环境中安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

完整网页使用外部 Data App 宿主，内容组件通过 `data-app-public.jsx` 接口接入。本仓库只包含自行编写的 `dashboard/src/content`，不分发宿主、受保护 UI、构建器或其二进制依赖。部分 JSX 组件测试也依赖原本机的 Data App 编译器。`dashboard/package.json` 仅提供 ES 模块标记，不能用它构建完整网页。

采集脚本使用固定的 Chrome DevTools MCP 连接器。`scripts/install_chrome_connector.py` 描述了该外部组件的下载和校验方式；其组件包、Node/Python 运行时均未随仓库分发。原保存流程还可能需要本机配置的 restic。请按使用的功能准备外部组件，并遵守各组件自身的许可条款。

`scripts/install_windows.py`、`open_saved_project.py` 和 `verify_project.py` 保留原本机交付与复核逻辑，需要原完整运行包的目录结构、清单、数据或运行时，**不是本公开源码版本的安装命令**。

## 基础测试

在仓库根目录执行以下只使用合成数据的基础检查，不需要店铺账号，也不会启动真实浏览器：

```powershell
python -B -m unittest tests.test_snapshot_json tests.test_sales_metric_alias tests.test_new_store_identity tests.test_shop_onboarding
node --test tests/test_atomic_collection_json.mjs tests/test_local_storefront.mjs tests/test_normal_chrome.mjs
node --test dashboard/tests/frontend-models.test.mjs dashboard/tests/shop-intake-options.test.mjs dashboard/tests/collection-progress.test.mjs
```

历史回放测试依赖未公开的本机采集证据，不能把这些测试视为可直接执行的基础测试。例如：

- `tests/test_store.py` 中读取历史基线的用例；
- `tests/review_capture_session.mjs` 的历史 42 批回放；
- `scripts/verify_project.py` 的完整历史验收链。

不要为了运行这些测试提交真实 `sources/` 或业务数据库。公开版本省略了 `scripts/review_live_capture_v4.mjs` 中唯一绑定实际店铺的历史回放用例，其余合成测试保留。其他测试如需要第三方编译器、完整交付清单或运行时，应先准备相应本机依赖。上述基础测试通过只证明对应代码行为，不等于真实平台全流程采集已通过。

## Skill 使用

`skills/pdd-collect/SKILL.md` 描述准确店铺目标、任务查询和按需 SKU 的调用规则。Skill 调用本机固定程序，不逐商品调用模型。

桥接脚本优先读取 `PDD_COLLECT_PROJECT` 环境变量；在仓库内使用时默认定位当前仓库根目录。需要可运行的本机项目及其 Python 运行时。复制 Skill 到其他位置时，请设置完整本机项目路径。部分 Skill 提及的本机操作文档不在本公开版中。

## 数据与凭据

本仓库不含业务数据库、采集快照、Cookie、TOKEN、API 密钥、用户配置、浏览器配置或备份。相关目录和文件已在 `.gitignore` 中排除。测试中的短链接、图片和凭据示例使用合成数据；切勿用真实凭据替换后提交。

仓库公开不自动授予额外许可；本次源码发布未附加新的开源许可证。
