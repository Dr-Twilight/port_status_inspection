# 设备巡检与端口状态基线检查工具

## 项目简介

本项目是一个综合性的网络设备管理工具，集成了**设备自动巡检**和**端口状态基线检查**两大核心功能。

1.  **设备自动巡检**：通过 `inspection_tool.py` 批量连接网络设备（如 H3C、Huawei），并发执行巡检命令，自动保存回显日志。
2.  **端口状态基线检查**：通过 `port_status_inspection.py` 对巡检日志进行深度分析，自动建立设备端口状态基线，并与新日志进行比对，及时发现端口状态（UP/DOWN）、STP 状态、LLDP 邻居等关键指标的异常变化。

用户可以通过统一入口 `main.py` 一键完成“巡检+分析”的全流程，也可以根据需要单独运行各个子模块。

---

## 功能特性

### 1. 自动化设备巡检 (`inspection_tool.py`)
*   **多厂商支持**：支持 H3C、Huawei 等主流网络设备。
*   **高并发执行**：内置线程池，支持数百台设备同时巡检，效率极高。
*   **安全可靠**：支持加密的 Excel 信息文件 (`info_port.xlsx`)，保护设备凭据安全。
*   **智能交互**：自动处理分页符（More）、命令纠错、长输出等待等复杂交互场景。
*   **日志归档**：按日期自动归档巡检日志，便于追溯。

### 2. 端口状态基线分析 (`port_status_inspection.py`)
*   **基线管理**：自动构建和维护设备端口状态基线（首次运行自动建立）。
*   **多维度对比**：
    *   **端口状态**：监控接口 UP/DOWN 变化。
    *   **STP 状态**：监控生成树角色（ROOT/DESI/ALTE）和状态（FORWARDING/DISCARDING）变化。
    *   **LLDP 邻居**：监控邻居设备变动，防止私接乱接。
*   **智能报告**：
    *   生成清晰的差异报告，仅展示有问题项。
    *   支持基线一致性检查，确保基线数据的准确性。
*   **灵活配置**：支持静默模式、详细模式、指定日志目录等多种运行方式。

---

## 快速开始

### 1. 环境要求
*   操作系统：Windows 7/10/11 或 Linux
*   Python 版本：3.8+
*   依赖库：见 `requirements.txt`

### 2. 安装依赖
```bash
pip install -r requirements.txt
```

### 3. 配置设备信息
在项目根目录下准备 `info_port.xlsx` 文件（支持加密），包含以下两个 Sheet：
*   **Sheet1 (设备列表)**：包含 `host` (设备名), `ip`, `protocol` (ssh/telnet), `username`, `password`, `port`, `secret` (enable密码), `device_type` (如 hp_comware, huawei) 等列。
*   **Sheet2 (巡检命令)**：按 `device_type` 列出需要执行的命令（如 `display interface brief`, `display lldp neighbor brief` 等）。

### 4. 运行程序

#### 方式一：全流程运行（推荐）
运行主程序，依次执行“设备巡检”和“日志分析”：
```bash
python main.py
```
*程序会提示输入 `info` 文件名（默认 `info_port.xlsx`）和 Excel 密码（如已加密）。*

#### 方式二：单独运行巡检
仅执行设备连接和日志抓取：
```bash
python inspection_tool.py
```

#### 方式三：单独运行分析
仅对已有的日志文件进行分析和基线对比：
```bash
python port_status_inspection.py
```

---

## 详细使用指南

### `port_status_inspection.py` 命令行参数

该模块支持丰富的命令行参数，用于定制分析行为：

| 参数 | 描述 | 默认值 |
| :--- | :--- | :--- |
| `--baseline-dir` | 指定基线文件夹路径 | `baseline` |
| `--log-dir` | 指定日志文件夹路径 | `logs` |
| `--mode` | 运行模式 (`consistency`, `index`, `compare`) | `compare` |
| `--quiet` | 静默模式，只输出关键结果 | `False` |
| `--verbose` | 详细模式，输出调试信息 | `False` |
| `--save-report` | 将分析报告保存为文件 | `False` |

**示例**：
```bash
# 仅检查基线一致性
python port_status_inspection.py --mode consistency

# 指定日志目录进行对比并保存报告
python port_status_inspection.py --log-dir logs --save-report
```

### 核心解析逻辑详解

本工具采用**上下文感知的状态机解析算法**，针对不同厂商设备的输出格式进行精准提取与标准化处理。这是确保基线对比准确性的核心机制。

#### 1. 端口状态标准化 (`parse_dis_int_brief`)

解析器会将不同厂商千差万别的原始输出，统一转换为标准化的状态字典，消除厂商差异：

*   **`admin_status` (管理状态)**: 标识端口是否被管理员手动关闭 (shutdown)。
    *   **判定逻辑**: 当检测到 `ADM` (H3C) 或 `*down` (Huawei) 时标记为 `DOWN`，否则为 `UP`。
*   **`line_status` (链路状态)**: 标识端口物理层及协议层是否完全连通。
    *   **判定逻辑**: 只有当物理状态和协议状态均为 `UP` 时才标记为 `UP`，任何一侧异常均视为 `DOWN`。

#### 2. H3C 设备深度解析 (双模式引擎)

H3C 设备通常将接口分为三层（路由）和二层（桥接）两种视图显示，工具实现了**分模式解析引擎**：

*   **Route Mode (三层接口)**
    *   **触发条件**: 扫描到 `Brief information on interfaces in route mode:`
    *   **解析策略**: 重点提取 `Protocol` 和 `Primary IP` 字段。
    *   **适用对象**: VLAN-interface, Loopback, Route-Aggregation 等。
    *   **代码实现**: 设置 `current_mode = "route"`，激活三层字段映射表。

*   **Bridge Mode (二层接口)**
    *   **触发条件**: 扫描到 `Brief information on interfaces in bridge mode:`
    *   **解析策略**: 重点提取 `Speed`, `Duplex`, `PVID` 等二层属性。
    *   **适用对象**: GigabitEthernet, Ten-GigabitEthernet 等物理口。
    *   **代码实现**: 设置 `current_mode = "bridge"`，激活二层字段映射表。

#### 3. Huawei 设备解析 (特性适配)

*   **自动表头识别**: 智能识别华为特有的表头 (`PHY`, `Protocol`, `InUti`, `OutUti`)。
*   **接口名归一化**: 内置映射表，自动将长接口名转换为简写（例如 `GigabitEthernet` -> `GE`, `Ten-GigabitEthernet` -> `XGE`），确保能与使用简写的基线或命令输出（如 `display interface description`）正确对齐。
*   **状态清洗**: 自动剥离状态字段中的特殊标记（如 `*down` 清洗为 `down`）。

#### 4. 鲁棒性与兼容设计

*   **智能边界检测**: 通过识别命令回显的起止特征（如命令行提示符 `<...>`），自动锁定 `display interface brief` 的有效输出范围，避免解析到无关的日志噪音。
*   **自适应降级**: 如果日志中缺失标准的“模式分隔符”（如旧版本固件），解析器会自动降级为通用模式，尝试通过列特征进行模糊匹配，最大程度保证解析成功率。

---

## 项目结构

```text
port_status_inspection/
├── main.py                     # [入口] 主程序，协调巡检和分析流程
├── inspection_tool.py          # [模块] 设备巡检工具 (执行命令, 保存日志)
├── port_status_inspection.py   # [模块] 端口状态分析工具 (基线对比)
├── info_port.xlsx              # [配置] 设备信息与命令配置文件
├── requirements.txt            # [配置] Python依赖库列表
├── packet_win7.bat             # [脚本] Windows打包脚本
├── README.md                   # [文档] 项目说明文档
├── logs/                       # [数据] 存放巡检日志
│   ├── 01log.log               # 运行错误日志
│   └── YYYY.MM.DD/             # 按日期归档的设备回显日志 (如 2025.12.16)
└── baseline/                   # [数据] 存放基线数据
    ├── baseline_index.json     # 基线索引文件
    └── YYYY_MM_DD/             # 按日期归档的基线文件 (如 2025_12_13)
```

---

## 打包说明

本项目包含 `packet_win7.bat` 脚本，可使用 PyInstaller 将程序打包为独立的可执行文件 (`.exe`)，方便在没有 Python 环境的 Windows 机器上运行。

1.  确保已安装 `pyinstaller`。
2.  双击运行 `packet_win7.bat`。
3.  打包完成后，在 `dist` 目录下会生成可执行文件。

---

## 注意事项

1.  **文件命名规范**：日志文件必须遵循 `[设备名]_[日期].log` 的格式（巡检工具会自动生成），否则分析工具无法正确识别。
2.  **基线更新**：首次运行时会自动建立基线。如果网络拓扑发生合法变更，请手动确认新的基线状态。
3.  **安全性**：建议对 `info_port.xlsx` 进行加密保护，防止设备密码泄露。本工具已内置解密支持。
