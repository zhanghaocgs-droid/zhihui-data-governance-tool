# 地质资料数据治理工具

指挥中心“地质调查管理业务数据治理与维护”项目的数据治理程序。把全国地质资料馆移交的档案按 GRP 落库规范重组为 项目 UUID → 九个项目阶段 的数据集，同时生成结构化目录 Excel、映射关系表、三套成果和质检结果。

当前版本 V1.0，适配现行报盘资料（XML 型 Edmaker2017、登记表型）。近期报盘、清欠期、早期图文在盘点时能识别，治理时标记暂不支持，分别在 V2.0、V3.0、V4.0 实现。设计见 [docs/开发设计说明.md](docs/开发设计说明.md)，作业人员请看 [docs/操作说明.md](docs/操作说明.md)。

## 运行环境

Windows 10 或 11，Python 3.11 及以上，单机离线。Word 转 PDF 依次尝试本机 Word、WPS、LibreOffice 便携版，都没有时 Word 文件进入待处理。

```bash
pip install -r requirements.txt
```

## 使用

图形界面：

```bash
python run_gui.py
```

首次启动选择工作区目录（存放数据库和成果区）并填写操作人。常规顺序为 档案盘点 页导入交接清单、导入 GRP 项目对照表、选择数据目录扫描，然后治理选中档或在 批量治理 页按档号区段运行，拿不准的文件到 人工复核 页确认，最后在 质量检查、成果输出 页校验并生成三套成果。

命令行：

```bash
python -m dgt.cli --ws 工作区 handover 地质资料数据交接清单.xlsx
python -m dgt.cli --ws 工作区 projects 项目对照表.xlsx
python -m dgt.cli --ws 工作区 scan 数据目录
python -m dgt.cli --ws 工作区 run --from 1 --to 99999
python -m dgt.cli --ws 工作区 qc
python -m dgt.cli --ws 工作区 export
```

## 目录

| 目录 | 内容 |
|---|---|
| dgt/ingest | 数据接入层：扫描盘点、交接清单、项目对照表 |
| dgt/adapt | 标准适配层：时期识别器、各时期解析器插件 |
| dgt/process | 治理处理层：项目关联、阶段分类、命名、转换、涉密识别 |
| dgt/output | 成果输出层：第六章校验、Excel、三套成果、质检 |
| dgt/pipeline.py | 按档执行八步流程，批次与断点续跑 |
| dgt/gui | 界面，八个模块 |
| rules | 规则配置，修改后提升版本号 |
| tests | 单元测试与样例回归测试 |

## 测试

```bash
python -m pytest
```

样例回归测试读取 `DGT_SAMPLE_ROOT` 指向的数据示例目录，默认为仓库同级的 `数据治理项目实施/数据示例`，找不到时自动跳过。快照只保存结构化结果，不含样例题名和项目名称。规则有意调整后设 `DGT_UPDATE_SNAPSHOT=1` 重跑更新快照，并在提交说明中写明原因。

## 数据安全

1）程序对原始区只读，所有写操作经 `WriteGuard` 校验，目标必须在成果区内。
2）仓库不包含任何移交资料、样例数据、数据库和成果，`.gitignore` 已排除。
3）非涉密成果输出前二次校验，发现涉密记录即中止该套输出。

## 打包

在联网开发机执行 `tools/build.ps1` 生成离线安装目录，依赖 wheel 用 `pip download -r requirements.txt -d wheels` 另行下载供甲方检查。
