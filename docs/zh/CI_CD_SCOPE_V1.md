# aish 第一版 CI/CD 功能范围

## 文档目标

本文档用于帮助团队讨论 **aish 第一版 CI/CD 应该包含哪些功能**。

> 当前先聚焦“有哪些功能要有”，暂不细分优先级，也不展开具体实现方案。

---

## 项目背景

结合当前仓库现状，aish 具备以下特征：

- Python CLI 项目
- 使用 `uv` 进行依赖与构建管理
- 已有 `pytest` 测试集
- 已有 Python 包构建能力
- 已有 PyInstaller 二进制构建能力
- 已有 Debian 打包路径

参考文件：

- [pyproject.toml](../../pyproject.toml)
- [uv.lock](../../uv.lock)
- [Makefile](../../Makefile)
- [build.sh](../../build.sh)
- [aish.spec](../../aish.spec)
- [packaging/build_deb.sh](../../packaging/build_deb.sh)
- [debian/rules](../../debian/rules)
- [tests](../../tests)

---

## 1. 自动构建（Build）

aish 的 CI/CD 第一版建议具备以下自动构建能力：

- 自动拉取代码
- 自动初始化 Python 环境
- 自动安装依赖
- 支持依赖缓存，加速重复执行
- 自动执行 Python 包构建
- 自动执行 PyInstaller 二进制构建
- 可选执行 Debian 包构建
- 保存构建产物，便于后续下载、验证和发布

对于 aish，构建相关的关键内容主要对应：

- Python 包构建： [pyproject.toml](../../pyproject.toml)
- 本地构建入口： [Makefile](../../Makefile)
- 二进制构建： [build.sh](../../build.sh)、[aish.spec](../../aish.spec)
- Debian 打包： [packaging/build_deb.sh](../../packaging/build_deb.sh)、[debian/rules](../../debian/rules)

---

## 2. 自动化测试（Test）

aish 的第一版 CI/CD 建议具备以下测试能力：

- 自动运行单元测试
- 自动运行集成测试
- 对关键 CLI 行为进行基础冒烟测试
- 对构建后的可执行产物进行基础运行验证
- 可输出测试报告
- 可输出覆盖率报告
- 测试失败时阻断 PR 合并

对于 aish，测试重点可以围绕以下内容：

- 主测试目录： [tests](../../tests)
- 安全相关测试： [tests/security](../../tests/security)
- CLI 入口： [src/aish/cli.py](../../src/aish/cli.py)、[src/aish/__main__.py](../../src/aish/__main__.py)
- 守护进程入口： [src/aish/sandboxd.py](../../src/aish/sandboxd.py)

---

## 3. 代码质量检查（Lint / Check）

aish 的第一版 CI/CD 建议具备以下质量检查能力：

- 代码风格检查
- 静态基础检查
- 类型检查
- 导入、未使用变量、明显错误检查
- 配置文件的基础合法性检查
- 文档中的关键命令与实际构建入口的一致性检查

当前仓库内和质量检查有关的主要文件包括：

- [pyproject.toml](../../pyproject.toml)
- [CONTRIBUTING.md](../../CONTRIBUTING.md)
- [QUICKSTART.md](../../QUICKSTART.md)

这一部分的目标是让代码在进入主分支之前，先通过基础的自动质量门禁。

---

## 4. 自动发布 / 部署（Release / Deploy）

对 aish 这类 CLI 工具项目来说，第一版更准确地说是“自动发布”，主要建议包含：

- 基于 Tag 触发正式发布
- 自动创建发布产物
- 自动上传构建产物到 Release 页面
- 支持发布 Python 包
- 支持发布 PyInstaller 二进制
- 支持发布 Debian 包
- 保留发布日志与失败信息

对于 aish，建议重点考虑以下发布物：

- Python 包
- PyInstaller 二进制
- Debian 包

对应文件：

- [pyproject.toml](../../pyproject.toml)
- [build.sh](../../build.sh)
- [aish.spec](../../aish.spec)
- [packaging/build_deb.sh](../../packaging/build_deb.sh)
- [debian](../../debian)

---

## 5. 版本与发布管理

aish 的第一版 CI/CD 建议具备以下版本与发布管理能力：

- 基于 Tag 做版本发布
- 自动校验版本号一致性
- 自动创建 GitHub Release
- 自动整理发布构件
- 自动关联本次发布的核心变更说明
- 保证包版本、发布版本、changelog 信息尽量一致

对 aish 来说，版本管理相关文件主要包括：

- [pyproject.toml](../../pyproject.toml)
- [debian/changelog](../../debian/changelog)
- [docs/en/release-notes](../../docs/en/release-notes)
- [docs/zh/release-notes](../../docs/zh/release-notes)

---

## 6. 通知、安全与流程控制

aish 的第一版 CI/CD 建议具备以下辅助能力：

### 通知与可观测性

- PR 检查状态可见
- 构建失败可见
- 发布失败可见
- 保留完整日志，便于快速定位问题
- 保留测试报告、覆盖率报告和构建产物

### 权限与安全

- 使用 GitHub Secrets 管理敏感信息
- 遵循最小权限原则
- 禁止在 CI/CD 中硬编码密钥
- 可增加依赖漏洞扫描
- 可增加密钥泄露扫描

### 流程控制

- 在 PR 场景自动运行检查
- 在 `main` 分支运行更完整的构建流程
- 在 Tag 场景运行正式发布流程
- 支持按场景执行不同任务
- 可视情况增加人工审批步骤

---

## 总结

如果只从第一版功能范围来看，aish 的 CI/CD 建议至少覆盖以下六类能力：

1. 自动构建
2. 自动化测试
3. 代码质量检查
4. 自动发布 / 部署
5. 版本与发布管理
6. 通知、安全与流程控制
