#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

from typing import List, Dict, Tuple
import re
import os
import sys
import glob

# 获取脚本所在目录的绝对路径
def get_base_dir():
    """
    动态获取脚本执行目录
    - 打包为exe时:返回exe所在目录
    - 脚本运行时：返回脚本文件所在目录
    """
    if getattr(sys, 'frozen', False):  # 判断是否为PyInstaller打包环境
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

# ==============================================
# 数据清理与预处理模块
# ==============================================

def _safe_str(x) -> str:
    """把 None/NaN/非字符串安全转换为字符串；NaN 返回空串。"""
    if x is None:
        return ""
    if isinstance(x, float):
        try:
            import math
            if math.isnan(x):  # 识别 pandas 的 NaN
                return ""
        except Exception:
            pass
        return str(x)  # 普通 float 显式转字符串，避免 .strip 报错
    return x if isinstance(x, str) else str(x)

def _strip_or_empty(x) -> str:
    """
    字符串清洗：
    - 去掉 Excel 遗留的 `_x000d_`
    - 去掉所有 \r \n \t 控制符
    - 去掉首尾空格
    """
    if not isinstance(x, str):
        return ""
    cleaned = x.replace("_x000d_", "")  # 移除 Excel 隐藏回车
    cleaned = re.sub(r"[\r\n\t]", "", cleaned)  # 移除控制符
    return cleaned.strip()

# ==============================================
# 设备类型检测模块
# ==============================================

def detect_device_type(output: str) -> str:
    """
    检测设备类型(H3C / Huawei)
    参数:
        output: 设备命令输出
    返回:
        str: 'huawei'、'h3c'或None(未知类型)
    """
    lines = output.split('\n')
    batch_size = 100  # 每次检查100行,平衡性能和准确性
    
    for i in range(0, len(lines), batch_size):
        batch = lines[i:i+batch_size]
        lower_batch = [line.lower() for line in batch]  # 统一转为小写，便于匹配
        
        for line in lower_batch:
            if 'huawei' in line:
                return 'huawei'
            if 'h3c' in line or 'new h3c technologies' in line:
                return 'h3c'
    return None

# ==============================================
# 命令输出解析核心模块
# ==============================================

def parse_dis_int_brief(output: str, device_type: str) -> Dict[str, Dict]:
    """
    解析 display interface brief 命令输出
    支持H3C和华为设备格式
    
    参数:
        output: 命令输出
        device_type: 设备类型 ('huawei' 或 'h3c')
    
    返回格式：
        - 华为格式: {port: {"admin_status": "UP/DOWN", "line_status": "UP/DOWN", "phy_status": "UP/DOWN", "protocol_status": "UP/DOWN", "in_uti": "0%", "out_uti": "0%", "in_errors": "0", "out_errors": "0"}}
        - 华三格式: {port: {"admin_status": "UP/DOWN", "line_status": "UP/DOWN", "link_status": "UP/DOWN/ADM", "protocol_status": "UP/DOWN", "mode": "route/bridge", "speed": "1G(a)", "duplex": "F(a)", "type": "A/T", "pvid": "1", "description": "To_xxx"}}
    """
    result = {}
    
    # 端口名称映射表，用于将完整名称转换为简写
    port_name_map = {
        "GigabitEthernet": "GE",
        "Ten-GigabitEthernet": "XGE",
        "XGigabitEthernet": "XGE",
        "TwentyFiveGigE": "25GE",
        "FortyGigE": "40GE",
        "HundredGigE": "100GE",
        "Eth-Trunk": "Eth-Trunk"
    }
    
    lines = output.split('\n')
    in_int_brief_section = False
    current_mode = None  # 用于H3C设备，记录当前是route mode还是bridge mode
    
    for line in lines:
        line = line.strip()
        
        # 定位display interface brief命令的输出范围
        if "display interface brief" in line or "dis int brief" in line:
            in_int_brief_section = True
            continue
        
        # 遇到下一个命令时，结束当前解析
        if in_int_brief_section and line.startswith('<') and '>' in line and 'dis' in line:
            break
        
        # 只处理display interface brief命令的输出内容
        if in_int_brief_section:
            # 处理H3C的模式切换
            if "Brief information on interfaces in route mode:" in line:
                current_mode = "route"
                continue
            elif "Brief information on interfaces in bridge mode:" in line:
                current_mode = "bridge"
                continue
            
            # 跳过空行
            if not line:
                continue
            
            # 华为格式解析
            if device_type == "huawei":
                # 跳过标题行
                if "PHY" in line or "Protocol" in line or "InUti" in line or "Interface" in line:
                    continue
                
                # 处理华为格式的端口状态行
                parts = line.split()
                if len(parts) >= 5:
                    # 处理缩进的子接口
                    if line.startswith('  ') and len(parts) >= 5:
                        full_port = parts[0]
                        phy_status = parts[1].strip().lower()
                        protocol_status = parts[2].strip().lower()
                        in_uti = parts[3].strip()
                        out_uti = parts[4].strip()
                    elif len(parts) >= 6:
                        # 主接口行
                        full_port = parts[0]
                        phy_status = parts[1].strip().lower()
                        protocol_status = parts[2].strip().lower()
                        in_uti = parts[3].strip()
                        out_uti = parts[4].strip()
                    else:
                        continue
                    
                    # 转换为简写格式
                    port = full_port
                    for full, short in port_name_map.items():
                        if full_port.startswith(full):
                            port = full_port.replace(full, short)
                            break
                    
                    # 判断管理状态和链路状态
                    if phy_status == "*down":
                        admin_status = "DOWN"
                        line_status = "DOWN"
                    elif phy_status == "down":
                        admin_status = "UP"
                        line_status = "DOWN"
                    else:
                        admin_status = "UP"
                        line_status = "UP" if protocol_status == "up" else "DOWN"
                    
                    # 构建华为格式端口信息字典
                    port_info = {
                        "admin_status": admin_status,
                        "line_status": line_status,
                        "phy_status": phy_status,
                        "protocol_status": protocol_status,
                        "in_uti": in_uti,
                        "out_uti": out_uti,
                        "in_errors": parts[5] if len(parts) >= 8 else "0",
                        "out_errors": parts[6] if len(parts) >= 8 else "0"
                    }
                    
                    result[port] = port_info
            
            # H3C格式解析
            elif device_type == "h3c":
                # 处理模式切换行
                if "Brief information on interfaces in route mode:" in line:
                    current_mode = "route"
                    continue
                elif "Brief information on interfaces in bridge mode:" in line:
                    current_mode = "bridge"
                    continue
                
                # 跳过标题行和说明行
                if "Link:" in line or "Speed:" in line or "Duplex:" in line or "Type:" in line:
                    continue
                
                # 跳过空行
                if not line:
                    continue
                
                # 处理H3C路由模式格式
                if current_mode == "route":
                    parts = line.split()
                    if len(parts) >= 3:
                        port = parts[0]
                        # 跳过表头行
                        if port == "Interface":
                            continue
                        link_status = parts[1].strip().upper()
                        protocol_status = parts[2].strip().upper()
                        
                        # 判断管理状态和链路状态
                        if link_status == "ADM" or link_status == "DOWN":
                            admin_status = "DOWN" if link_status == "ADM" else "UP"
                            line_status = "DOWN"
                        else:
                            admin_status = "UP"
                            line_status = "UP"
                        
                        # 构建华三格式端口信息字典
                        port_info = {
                            "admin_status": admin_status,
                            "line_status": line_status,
                            "link_status": link_status,
                            "protocol_status": protocol_status,
                            "mode": current_mode
                        }
                        
                        # 添加路由模式特有字段
                        if len(parts) >= 4:
                            port_info["primary_ip"] = parts[3] if parts[3] != "--" else ""
                        if len(parts) >= 5:
                            port_info["description"] = " ".join(parts[4:])
                        
                        result[port] = port_info
                
                # 处理H3C桥接模式格式
                elif current_mode == "bridge":
                    parts = line.split()
                    if len(parts) >= 6:
                        port = parts[0]
                        # 跳过表头行
                        if port == "Interface":
                            continue
                        link_status = parts[1].strip().upper()
                        
                        # 判断管理状态和链路状态
                        if link_status == "ADM" or link_status == "DOWN":
                            admin_status = "DOWN" if link_status == "ADM" else "UP"
                            line_status = "DOWN"
                        else:
                            admin_status = "UP"
                            line_status = "UP"
                        
                        # 构建华三格式端口信息字典
                        port_info = {
                            "admin_status": admin_status,
                            "line_status": line_status,
                            "link_status": link_status,
                            "protocol_status": "UP" if link_status == "UP" else "DOWN",
                            "mode": current_mode,
                            "speed": parts[2] if len(parts) > 2 else "",
                            "duplex": parts[3] if len(parts) > 3 else "",
                            "type": parts[4] if len(parts) > 4 else "",
                            "pvid": parts[5] if len(parts) > 5 else ""
                        }
                        
                        # 添加桥接模式特有字段
                        if len(parts) > 6:
                            port_info["description"] = " ".join(parts[6:])
                        
                        result[port] = port_info
                
                # 如果没有明确的mode，尝试自动检测
                else:
                    # 跳过表头行
                    if line.startswith("Interface"):
                        continue
                    
                    parts = line.split()
                    if len(parts) >= 4:
                        port = parts[0]
                        # 跳过表头行
                        if port == "Interface":
                            continue
                        link_status = parts[1].strip().upper()
                        
                        # 判断管理状态和链路状态
                        if link_status == "ADM":
                            admin_status = "DOWN"
                            line_status = "DOWN"
                        elif link_status == "DOWN":
                            admin_status = "UP"
                            line_status = "DOWN"
                        else:
                            admin_status = "UP"
                            line_status = "UP"
                        
                        # 构建华三格式端口信息字典
                        port_info = {
                            "admin_status": admin_status,
                            "line_status": line_status,
                            "link_status": link_status,
                            "protocol_status": parts[2].strip().upper() if len(parts) >= 3 else "DOWN"
                        }
                        
                        result[port] = port_info
    
    # 如果没有找到 display interface brief 命令的开始行，尝试全局匹配（兼容旧版本日志）
    if not result:
        for line in lines:
            line = line.strip()
            
            # 跳过标题行、空行和其他命令行
            if not line or "Interface" in line or "PHY:" in line or "Link:" in line or "InUti" in line or "Brief information" in line:
                continue
            
            # 尝试华为格式匹配
            parts = line.split()
            if len(parts) >= 6:
                if parts[1] in ["up", "down", "*down"] and parts[2] in ["up", "down"]:
                    full_port = parts[0]
                    # 转换为简写格式
                    port = full_port
                    for full, short in port_name_map.items():
                        if full_port.startswith(full):
                            port = full_port.replace(full, short)
                            break
                    # 提取状态信息
                    phy_status = parts[1].strip().lower()
                    protocol_status = parts[2].strip().lower()
                    in_uti = parts[3].strip()
                    out_uti = parts[4].strip()
                    
                    # 判断管理状态和链路状态
                    if phy_status == "*down":
                        admin_status = "DOWN"
                        line_status = "DOWN"
                    elif phy_status == "down":
                        admin_status = "UP"
                        line_status = "DOWN"
                    else:
                        admin_status = "UP"
                        line_status = "UP" if protocol_status == "up" else "DOWN"
                    
                    # 构建华为格式端口信息字典
                    port_info = {
                        "admin_status": admin_status,
                        "line_status": line_status,
                        "phy_status": phy_status,
                        "protocol_status": protocol_status,
                        "in_uti": in_uti,
                        "out_uti": out_uti,
                        "in_errors": parts[5] if len(parts) >= 8 else "0",
                        "out_errors": parts[6] if len(parts) >= 8 else "0"
                    }
                    
                    result[port] = port_info
            
            # 尝试H3C格式匹配
            parts = line.split()
            if len(parts) >= 4:
                if parts[1] in ["UP", "DOWN", "ADM"]:
                    port = parts[0]
                    link_status = parts[1].strip().upper()
                    
                    # 判断管理状态和链路状态
                    if link_status == "ADM":
                        admin_status = "DOWN"
                        line_status = "DOWN"
                    elif link_status == "DOWN":
                        admin_status = "UP"
                        line_status = "DOWN"
                    else:
                        admin_status = "UP"
                        line_status = "UP"
                    
                    # 构建华三格式端口信息字典
                    port_info = {
                        "admin_status": admin_status,
                        "line_status": line_status,
                        "link_status": link_status,
                        "protocol_status": parts[2].strip().upper() if len(parts) >= 3 else "DOWN"
                    }
                    
                    result[port] = port_info
    
    return result

def parse_dis_lldp_neighbor(output: str) -> Dict[str, Dict]:
    """
    解析 display lldp neighbor list 或 display lldp neighbor brief 命令输出
    支持H3C和华为设备格式
    返回格式：{port: {"has_neighbor": True/False, "neighbor_dev": "设备名称", "neighbor_port": "端口号"}}
    """
    result = {}
    lines = output.split('\n')
    in_lldp_list_section = False
    is_huawei_format = False  # 标记是否为华为格式
    
    for line in lines:
        line = line.strip()
        
        # 找到LLDP邻居列表的开始行
        if "LocalIf         Nbr chassis ID" in line or "Local Interface Chassis ID" in line:
            in_lldp_list_section = True
            is_huawei_format = False
            continue
        elif "Local Intf   Neighbor Dev             Neighbor Intf             Exptime(s)" in line:
            in_lldp_list_section = True
            is_huawei_format = True
            continue
        
        # 处理LLDP邻居列表行
        if in_lldp_list_section and line:
            parts = line.split()
            if parts:
                local_if = parts[0]
                
                # 只处理GE和XGE类型的端口
                if re.match(r'^(?:GE|XGE)\d+/\d+/\d+$', local_if):
                    neighbor_info = {
                        "has_neighbor": True,
                        "neighbor_dev": "",
                        "neighbor_port": ""
                    }
                    
                    if len(parts) >= 4:
                        if is_huawei_format:
                            # 华为格式：Local Intf   Neighbor Dev             Neighbor Intf             Exptime(s)
                            neighbor_info["neighbor_dev"] = parts[1]
                            neighbor_info["neighbor_port"] = parts[2]
                        else:
                            # H3C格式：LocalIf         Nbr chassis ID  Nbr Port ID          Nbr System Name
                            neighbor_info["neighbor_dev"] = parts[3]
                            neighbor_info["neighbor_port"] = parts[2]
                    
                    result[local_if] = neighbor_info
        
        # 遇到空行或命令提示符，结束LLDP列表部分
        if in_lldp_list_section and (not line or '<' in line and '>' in line):
            break
    
    return result

def parse_dis_stp_brief(output: str) -> Dict[str, Dict]:
    """
    解析 display stp brief 命令输出
    支持H3C和华为设备格式
    返回格式：{port: {"role": "DESI/ROOT/ALTE/BACK", "stp_state": "FORWARDING/LEARNING/DISCARDING"}}
    """
    result = {}
    
    # 端口名称映射表，用于将完整名称转换为简写
    port_name_map = {
        "GigabitEthernet": "GE",
        "Ten-GigabitEthernet": "XGE",
        "TwentyFiveGigE": "25GE",
        "FortyGigE": "40GE",
        "HundredGigE": "100GE"
    }
    
    # 匹配STP状态行
    stp_lines = []
    # 找到STP brief输出的开始行
    in_stp_section = False
    for line in output.split('\n'):
        line = line.strip()
        if "MST ID   Port" in line or "MSTID   Port" in line:
            in_stp_section = True
            continue
        if in_stp_section and line:
            stp_lines.append(line)
        elif in_stp_section and not line:
            # 遇到空行，结束STP section
            break
    
    # 解析STP状态行
    for line in stp_lines:
        # 跳过标题行
        if "Role" in line or "STP State" in line:
            continue
        
        # 使用正则表达式匹配STP状态行，处理不同设备的格式差异
        # 匹配MSTID、端口名、角色、STP状态
        stp_pattern = r'\s*\d+\s+(\w+\d+/\d+/\d+)\s+(\w+)\s+(\w+)'
        match = re.match(stp_pattern, line, re.IGNORECASE)
        if match:
            full_port = match.group(1)
            role = match.group(2).upper()  # 角色转换为大写
            stp_state = match.group(3).upper()  # STP状态转换为大写
            
            # 只接受有效的STP状态
            if stp_state in ["FORWARDING", "LEARNING", "DISCARDING", "LISTENING", "BLOCKING"]:
                # 将完整端口名转换为简写格式
                short_port = full_port
                for full, short in port_name_map.items():
                    if full_port.startswith(full):
                        short_port = full_port.replace(full, short)
                        break
                # 返回包含角色和状态的字典
                result[short_port] = {
                    "role": role,
                    "stp_state": stp_state
                }
    
    return result

# ==============================================
# 命令解析调度模块
# ==============================================

def parse_port_status(output: str, device_type: str, cmd: str) -> Dict:
    """
    根据命令类型解析端口状态信息
    返回相应的解析结果
    """
    cmd_clean = _strip_or_empty(cmd).lower()
    
    if "dis int brief" in cmd_clean or "display interface brief" in cmd_clean:
        return parse_dis_int_brief(output, device_type)
    elif "dis lldp n" in cmd_clean or "display lldp neighbor" in cmd_clean:
        return parse_dis_lldp_neighbor(output)
    elif "dis stp brief" in cmd_clean or "display stp brief" in cmd_clean:
        return parse_dis_stp_brief(output)
    else:
        # 原始解析逻辑，用于其他命令
        ports = []
        # 华为/H3C设备解析逻辑
        if device_type in ['huawei', 'h3c', 'hp_comware']:
            port_pattern = r'(\w+\d+/\d+/\d+)\s+current\s+state\s*:\s*(\w+(?:\s+\w+)*)'
            matches = re.finditer(port_pattern, output, re.IGNORECASE)
            
            for match in matches:
                port = match.group(1)
                status = match.group(2).strip().upper()
                ports.append({
                    'port': port,
                    'status': status,
                    'description': ''
                })
        
        # Cisco设备解析逻辑
        elif device_type in ['cisco_ios', 'cisco_xe']:
            port_pattern = r'(\w+\d+/\d+/\d+)\s+is\s+(\w+(?:\s+\w+)*)'
            matches = re.finditer(port_pattern, output, re.IGNORECASE)
            
            for match in matches:
                port = match.group(1)
                status = match.group(2).split(',')[0].strip().upper()
                ports.append({
                    'port': port,
                    'status': status,
                    'description': ''
                })
        
        # 其他设备类型
        else:
            port_pattern = r'(\w+\d+[\w/]*\d+)\s+\w+\s+(\w+(?:\s+\w+)*)'
            matches = re.finditer(port_pattern, output, re.IGNORECASE)
            
            for match in matches:
                port = match.group(1)
                status = match.group(2).strip().upper()
                ports.append({
                    'port': port,
                    'status': status,
                    'description': ''
                })
        return ports

# ==============================================
# 测试模块
# ==============================================

def get_device_name(file_path):
    """
    从文件路径中提取设备名称
    例如：从"内网[区局核心交换机1]_[2025_12_12].log"提取"区局核心交换机1"
    """
    file_name = os.path.basename(file_path)
    match = re.match(r'\[(.*?)\]', file_name)
    if match:
        return match.group(1)
    return file_name

def parse_log_file(file_path):
    """
    解析单个日志文件
    """
    device_name = get_device_name(file_path)
    print(f"\n=== 测试设备: {device_name} ===")
    
    # 读取日志文件内容
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 检测设备类型
    device_type = detect_device_type(content)
    print(f"\n--- 设备类型检测结果: {device_type} ---")
    
    # 测试parse_dis_int_brief函数
    print("\n--- parse_dis_int_brief 函数测试结果 ---")
    int_result = parse_dis_int_brief(content, device_type)
    if int_result:
        for port, status in int_result.items():
            print(f"  {port}: {status}")
    else:
        print("  未解析到接口状态信息")
    
    # 测试parse_dis_lldp_neighbor函数
    print("\n--- parse_dis_lldp_neighbor 函数测试结果 ---")
    lldp_result = parse_dis_lldp_neighbor(content)
    if lldp_result:
        for port, info in lldp_result.items():
            if info["has_neighbor"]:
                print(f"  {port}: 邻居设备: {info['neighbor_dev']} | 邻居端口: {info['neighbor_port']}")
            else:
                print(f"  {port}: 无邻居")
    else:
        print("  未解析到LLDP邻居信息")
    
    # 测试parse_dis_stp_brief函数
    print("\n--- parse_dis_stp_brief 函数测试结果 ---")
    stp_result = parse_dis_stp_brief(content)
    if stp_result:
        for port, info in stp_result.items():
            print(f"  {port}: 角色={info['role']}, 状态={info['stp_state']}")
    else:
        print("  未解析到STP状态信息")

def main():
    """
    主函数
    """
    # 获取当前脚本所在目录
    script_dir = get_base_dir()
    # 定义内网文件夹路径
    inner_network_dir = os.path.join(script_dir, "内网")
    
    # 检查内网文件夹是否存在
    if not os.path.exists(inner_network_dir):
        print(f"错误：内网文件夹 {inner_network_dir} 不存在")
        return
    
    # 获取内网文件夹下的所有日志文件
    log_files = glob.glob(os.path.join(inner_network_dir, "*.log"))
    if not log_files:
        print(f"错误：内网文件夹 {inner_network_dir} 下没有日志文件")
        return
    
    # 遍历所有日志文件并测试
    for log_file in log_files:
        parse_log_file(log_file)
    
    print("\n=== 所有设备测试完成 ===")

# ==============================================
# 基线管理模块
# ==============================================
import json
import time

class BaselineManager:
    def __init__(self, baseline_root="baseline"):
        """初始化基线管理器"""
        self.baseline_root = baseline_root
        self.index_file = os.path.join(baseline_root, "baseline_index.json")
        self.load_index()
    
    def load_index(self):
        """加载或初始化基线索引"""
        if os.path.exists(self.index_file):
            with open(self.index_file, "r") as f:
                self.index = json.load(f)
        else:
            self.index = {
                "latest_baseline": None,
                "baseline_history": [],
                "devices": {}
            }
    
    def save_index(self):
        """保存基线索引"""
        # 确保基线根目录存在
        if not os.path.exists(self.baseline_root):
            os.makedirs(self.baseline_root)
        with open(self.index_file, "w") as f:
            json.dump(self.index, f, indent=2, ensure_ascii=False)
    
    def get_baseline_dates(self):
        """获取所有基线时间文件夹，按时间排序"""
        dates = []
        if not os.path.exists(self.baseline_root):
            return dates
        
        for item in os.listdir(self.baseline_root):
            item_path = os.path.join(self.baseline_root, item)
            if os.path.isdir(item_path) and re.match(r'^\d{4}_\d{2}_\d{2}$', item):
                dates.append(item)
        # 按时间排序，最新的在最后
        return sorted(dates)
    
    def get_latest_baseline(self):
        """获取最新基线时间"""
        dates = self.get_baseline_dates()
        return dates[-1] if dates else None
    
    def get_devices_in_baseline(self, baseline_date):
        """获取指定基线中的所有设备日志"""
        baseline_path = os.path.join(self.baseline_root, baseline_date)
        if not os.path.exists(baseline_path):
            return []
        
        device_logs = []
        for log_file in os.listdir(baseline_path):
            if log_file.endswith(".log"):
                # 优化设备名称提取逻辑，只匹配设备名称部分
                match = re.match(r'\[(.*?)\]', log_file)
                if match:
                    device_logs.append({
                        "file_name": log_file,
                        "device_name": match.group(1),
                        "file_path": os.path.join(baseline_path, log_file)
                    })
        return device_logs
    
    def extract_device_status(self, log_path):
        """从日志文件中提取设备状态"""
        with open(log_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        device_type = detect_device_type(content)
        if not device_type:
            return None
        
        return {
            "device_type": device_type,
            "port_status": parse_dis_int_brief(content, device_type),
            "stp_status": parse_dis_stp_brief(content),
            "lldp_status": parse_dis_lldp_neighbor(content)
        }
    
    def compare_baseline_consistency(self):
        """比较所有基线的一致性"""
        baseline_dates = self.get_baseline_dates()
        if len(baseline_dates) < 2:
            return {
                "status": "info",
                "message": "基线数量不足，无法进行一致性检查",
                "baseline_count": len(baseline_dates)
            }
        
        latest_date = baseline_dates[-1]
        old_dates = baseline_dates[:-1]
        
        # 获取所有基线中的设备映射
        device_mapping = self.build_device_mapping()
        
        # 获取最新基线中的所有设备
        latest_devices = self.get_devices_in_baseline(latest_date)
        
        consistency_results = {
            "latest_baseline": latest_date,
            "old_baselines": old_dates,
            "baseline_dates": baseline_dates,
            "total_devices": len(latest_devices),
            "device_mapping": device_mapping,
            "consistency_issues": [],
            "missing_devices": []
        }
        
        # 遍历最新基线中的每个设备
        for device in latest_devices:
            device_name = device["device_name"]
            latest_status = self.extract_device_status(device["file_path"])
            
            if not latest_status:
                consistency_results["consistency_issues"].append({
                    "device_name": device_name,
                    "issue_type": "parse_error",
                    "description": f"最新基线日志解析失败: {device['file_name']}"
                })
                continue
            
            # 检查该设备在所有旧基线中是否存在
            for old_date in old_dates:
                old_devices = self.get_devices_in_baseline(old_date)
                old_device = next((d for d in old_devices if d["device_name"] == device_name), None)
                
                if not old_device:
                    consistency_results["missing_devices"].append({
                        "device_name": device_name,
                        "missing_in": old_date
                    })
                    continue
                
                old_status = self.extract_device_status(old_device["file_path"])
                if not old_status:
                    consistency_results["consistency_issues"].append({
                        "device_name": device_name,
                        "issue_type": "parse_error",
                        "description": f"旧基线日志解析失败: {old_device['file_name']}"
                    })
                    continue
                
                # 对比端口状态一致性
                for port in latest_status["port_status"]:
                    if port in old_status["port_status"]:
                        latest_port = latest_status["port_status"][port]
                        old_port = old_status["port_status"][port]
                        
                        # 检查管理状态和链路状态一致性
                        if latest_port["admin_status"] != old_port["admin_status"]:
                            consistency_results["consistency_issues"].append({
                                "device_name": device_name,
                                "issue_type": "port_status_inconsistent",
                                "description": f"端口{port}管理状态不一致: {old_date}={old_port['admin_status']}, {latest_date}={latest_port['admin_status']}"
                            })
                        
                        if latest_port["line_status"] != old_port["line_status"]:
                            consistency_results["consistency_issues"].append({
                                "device_name": device_name,
                                "issue_type": "port_status_inconsistent",
                                "description": f"端口{port}链路状态不一致: {old_date}={old_port['line_status']}, {latest_date}={latest_port['line_status']}"
                            })
                    else:
                        consistency_results["consistency_issues"].append({
                            "device_name": device_name,
                            "issue_type": "port_missing",
                            "description": f"端口{port}在{old_date}基线中不存在"
                        })
                
                # 对比STP状态一致性
                for port in latest_status["stp_status"]:
                    if port in old_status["stp_status"]:
                        latest_stp = latest_status["stp_status"][port]
                        old_stp = old_status["stp_status"][port]
                        
                        # STP状态必须为FORWARDING
                        if latest_stp["stp_state"] != "FORWARDING":
                            consistency_results["consistency_issues"].append({
                                "device_name": device_name,
                                "issue_type": "stp_state_invalid",
                                "description": f"端口{port}STP状态异常: {latest_stp['stp_state']}"
                            })
                        if old_stp["stp_state"] != "FORWARDING":
                            consistency_results["consistency_issues"].append({
                                "device_name": device_name,
                                "issue_type": "stp_state_invalid",
                                "description": f"端口{port}在{old_date}基线中STP状态异常: {old_stp['stp_state']}"
                            })
                    else:
                        consistency_results["consistency_issues"].append({
                            "device_name": device_name,
                            "issue_type": "stp_missing",
                            "description": f"端口{port}STP状态在{old_date}基线中不存在"
                        })
                
                # 对比LLDP状态一致性
                for port in latest_status["lldp_status"]:
                    if port in old_status["lldp_status"]:
                        latest_lldp = latest_status["lldp_status"][port]
                        old_lldp = old_status["lldp_status"][port]
                        
                        # 检查邻居存在性一致性
                        if latest_lldp["has_neighbor"] != old_lldp["has_neighbor"]:
                            consistency_results["consistency_issues"].append({
                                "device_name": device_name,
                                "issue_type": "lldp_neighbor_inconsistent",
                                "description": f"端口{port}邻居存在性不一致: {old_date}={old_lldp['has_neighbor']}, {latest_date}={latest_lldp['has_neighbor']}"
                            })
                        
                        # 检查邻居设备一致性
                        if (latest_lldp["has_neighbor"] and old_lldp["has_neighbor"] and \
                            latest_lldp["neighbor_dev"] != old_lldp["neighbor_dev"]):
                            consistency_results["consistency_issues"].append({
                                "device_name": device_name,
                                "issue_type": "lldp_neighbor_dev_inconsistent",
                                "description": f"端口{port}邻居设备不一致: {old_date}={old_lldp['neighbor_dev']}, {latest_date}={latest_lldp['neighbor_dev']}"
                            })
                        
                        # 检查邻居端口一致性
                        if (latest_lldp["has_neighbor"] and old_lldp["has_neighbor"] and \
                            latest_lldp["neighbor_port"] != old_lldp["neighbor_port"]):
                            consistency_results["consistency_issues"].append({
                                "device_name": device_name,
                                "issue_type": "lldp_neighbor_port_inconsistent",
                                "description": f"端口{port}邻居端口不一致: {old_date}={old_lldp['neighbor_port']}, {latest_date}={latest_lldp['neighbor_port']}"
                            })
                    else:
                        consistency_results["consistency_issues"].append({
                            "device_name": device_name,
                            "issue_type": "lldp_missing",
                            "description": f"端口{port}LLDP状态在{old_date}基线中不存在"
                        })
        
        return consistency_results
    
    def generate_consistency_report(self, consistency_results, verbose=False):
        """生成可读性强的一致性报告"""
        report = []
        report.append("="*60)
        report.append("基线一致性检查报告")
        report.append("="*60)
        
        # 检查是否为信息性结果
        if 'status' in consistency_results and consistency_results['status'] == 'info':
            report.append(f"状态: {consistency_results['status']}")
            report.append(f"消息: {consistency_results['message']}")
            report.append(f"基线数量: {consistency_results['baseline_count']}")
        else:
            # 正常一致性检查结果
            report.append(f"最新基线: {consistency_results['latest_baseline']}")
            report.append(f"旧基线数量: {len(consistency_results['old_baselines'])}")
            report.append(f"设备总数: {consistency_results['total_devices']}")
            
            # 统计问题数量
            total_issues = len(consistency_results['consistency_issues']) + len(consistency_results['missing_devices'])
            has_issues = total_issues > 0
            
            # 只有在有问题或verbose模式下才显示设备对应情况
            if has_issues or verbose:
                report.append("\n")
                report.append("1. 设备对应情况:")
                report.append("-"*40)
                # 获取最新基线中的设备
                latest_devices = self.get_devices_in_baseline(consistency_results['latest_baseline'])
                # 获取所有基线日期
                all_dates = consistency_results['baseline_dates']
                
                # 如果有device_mapping，使用device_mapping显示设备对应情况
                if 'device_mapping' in consistency_results and consistency_results['device_mapping']:
                    for device_name, info in consistency_results['device_mapping'].items():
                        # 检查该设备是否有问题
                        device_has_issues = any(missing['device_name'] == device_name for missing in consistency_results['missing_devices']) or \
                                           any(issue['device_name'] == device_name for issue in consistency_results['consistency_issues'])
                        # 只有在有问题或verbose模式下才显示该设备
                        if device_has_issues or verbose:
                            report.append(f"  {device_name}:")
                            for date in all_dates:
                                if date in info['baseline_files']:
                                    report.append(f"    - {date}: {info['baseline_files'][date]}")
                                else:
                                    report.append(f"    - {date}: 缺失")
                # 否则，直接从最新基线设备中提取设备名称并显示
                elif latest_devices:
                    # 提取所有设备名称
                    device_names = list(set([d["device_name"] for d in latest_devices]))
                    for device_name in sorted(device_names):
                        # 检查该设备是否有问题
                        device_has_issues = any(missing['device_name'] == device_name for missing in consistency_results['missing_devices']) or \
                                           any(issue['device_name'] == device_name for issue in consistency_results['consistency_issues'])
                        # 只有在有问题或verbose模式下才显示该设备
                        if device_has_issues or verbose:
                            report.append(f"  {device_name}:")
                            for date in all_dates:
                                # 检查该设备在当前日期基线中是否存在
                                date_devices = self.get_devices_in_baseline(date)
                                device_in_date = next((d for d in date_devices if d["device_name"] == device_name), None)
                                if device_in_date:
                                    report.append(f"    - {date}: {device_in_date['file_name']}")
                                else:
                                    report.append(f"    - {date}: 缺失")
                else:
                    report.append("  未检测到设备")
                report.append("\n")
            
            # 只有在有问题时才显示缺失的设备日志
            if consistency_results['missing_devices']:
                report.append("2. 缺失的设备日志:")
                report.append("-"*40)
                for missing in consistency_results['missing_devices']:
                    report.append(f"  {missing['device_name']} 在 {missing['missing_in']} 基线中缺失")
                report.append("\n")
            
            # 只有在有问题时才显示一致性问题
            if consistency_results['consistency_issues']:
                report.append("3. 一致性问题:")
                report.append("-"*40)
                for issue in consistency_results['consistency_issues']:
                    report.append(f"  [{issue['issue_type']}] {issue['device_name']}: {issue['description']}")
                report.append("\n")
            
            # 统计信息
            if total_issues == 0:
                report.append("4. 一致性状态: 全部一致 ✓")
            else:
                report.append(f"4. 一致性状态: 发现 {total_issues} 个问题 ✗")
        
        report.append("="*60)
        
        return "\n".join(report)
    
    def compare_with_baseline(self, log_path):
        """将日志文件与最新基线进行对比"""
        # 1. 检查基线一致性
        consistency_results = self.compare_baseline_consistency()
        if 'status' in consistency_results and consistency_results['status'] == 'info':
            return {
                'status': 'error',
                'message': consistency_results['message']
            }
        
        # 2. 解析日志文件
        device_name = get_device_name(log_path)
        with open(log_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        device_type = detect_device_type(content)
        if not device_type:
            return {
                'status': 'error',
                'message': f'无法检测设备类型: {log_path}'
            }
        
        log_status = {
            'device_type': device_type,
            'port_status': parse_dis_int_brief(content, device_type),
            'stp_status': parse_dis_stp_brief(content),
            'lldp_status': parse_dis_lldp_neighbor(content)
        }
        
        # 3. 获取最新基线中的对应设备状态
        latest_baseline = consistency_results['latest_baseline']
        latest_devices = self.get_devices_in_baseline(latest_baseline)
        baseline_device = next((d for d in latest_devices if d['device_name'] == device_name), None)
        
        if not baseline_device:
            return {
                'status': 'error',
                'message': f'最新基线中未找到设备: {device_name}'
            }
        
        baseline_status = self.extract_device_status(baseline_device['file_path'])
        
        # 4. 对比日志与基线状态
        comparison_results = self.compare_device_status(log_status, baseline_status, device_name)
        
        return {
            'status': 'success',
            'device_name': device_name,
            'latest_baseline': latest_baseline,
            'comparison': comparison_results
        }
    
    def compare_device_status(self, log_status, baseline_status, device_name):
        """对比设备状态"""
        results = {
            'port_differences': [],
            'stp_differences': [],
            'lldp_differences': []
        }
        
        # 对比端口状态
        log_ports = log_status['port_status']
        baseline_ports = baseline_status['port_status']
        
        # 检查新增端口
        for port in log_ports:
            if port not in baseline_ports:
                results['port_differences'].append({
                    'type': 'new_port',
                    'port': port,
                    'status': log_ports[port]
                })
            else:
                # 检查端口状态变化
                log_port_status = log_ports[port]
                baseline_port_status = baseline_ports[port]
                
                # 检查管理状态和链路状态变化
                if log_port_status.get('admin_status') != baseline_port_status.get('admin_status'):
                    results['port_differences'].append({
                        'type': 'admin_status_change',
                        'port': port,
                        'old_status': baseline_port_status.get('admin_status'),
                        'new_status': log_port_status.get('admin_status')
                    })
                
                if log_port_status.get('line_status') != baseline_port_status.get('line_status'):
                    results['port_differences'].append({
                        'type': 'line_status_change',
                        'port': port,
                        'old_status': baseline_port_status.get('line_status'),
                        'new_status': log_port_status.get('line_status')
                    })
        
        # 检查缺失端口
        for port in baseline_ports:
            if port not in log_ports:
                results['port_differences'].append({
                    'type': 'missing_port',
                    'port': port,
                    'status': baseline_ports[port]
                })
        
        # 对比STP状态
        log_stp = log_status['stp_status']
        baseline_stp = baseline_status['stp_status']
        
        # 检查新增STP端口
        for port in log_stp:
            if port not in baseline_stp:
                results['stp_differences'].append({
                    'type': 'new_stp_port',
                    'port': port,
                    'status': log_stp[port]
                })
            else:
                # 检查STP状态变化
                log_stp_status = log_stp[port]
                baseline_stp_status = baseline_stp[port]
                
                if log_stp_status.get('stp_state') != baseline_stp_status.get('stp_state'):
                    results['stp_differences'].append({
                        'type': 'stp_state_change',
                        'port': port,
                        'old_status': baseline_stp_status.get('stp_state'),
                        'new_status': log_stp_status.get('stp_state')
                    })
                
                if log_stp_status.get('role') != baseline_stp_status.get('role'):
                    results['stp_differences'].append({
                        'type': 'stp_role_change',
                        'port': port,
                        'old_status': baseline_stp_status.get('role'),
                        'new_status': log_stp_status.get('role')
                    })
        
        # 检查缺失STP端口
        for port in baseline_stp:
            if port not in log_stp:
                results['stp_differences'].append({
                    'type': 'missing_stp_port',
                    'port': port,
                    'status': baseline_stp[port]
                })
        
        # 对比LLDP状态
        log_lldp = log_status['lldp_status']
        baseline_lldp = baseline_status['lldp_status']
        
        # 检查新增LLDP端口
        for port in log_lldp:
            if port not in baseline_lldp:
                results['lldp_differences'].append({
                    'type': 'new_lldp_port',
                    'port': port,
                    'status': log_lldp[port]
                })
            else:
                # 检查LLDP状态变化
                log_lldp_status = log_lldp[port]
                baseline_lldp_status = baseline_lldp[port]
                
                if log_lldp_status.get('has_neighbor') != baseline_lldp_status.get('has_neighbor'):
                    results['lldp_differences'].append({
                        'type': 'lldp_neighbor_presence_change',
                        'port': port,
                        'old_status': baseline_lldp_status.get('has_neighbor'),
                        'new_status': log_lldp_status.get('has_neighbor')
                    })
                
                if (log_lldp_status.get('has_neighbor') and baseline_lldp_status.get('has_neighbor') and \
                    log_lldp_status.get('neighbor_dev') != baseline_lldp_status.get('neighbor_dev')):
                    results['lldp_differences'].append({
                        'type': 'lldp_neighbor_dev_change',
                        'port': port,
                        'old_status': baseline_lldp_status.get('neighbor_dev'),
                        'new_status': log_lldp_status.get('neighbor_dev')
                    })
                
                if (log_lldp_status.get('has_neighbor') and baseline_lldp_status.get('has_neighbor') and \
                    log_lldp_status.get('neighbor_port') != baseline_lldp_status.get('neighbor_port')):
                    results['lldp_differences'].append({
                        'type': 'lldp_neighbor_port_change',
                        'port': port,
                        'old_status': baseline_lldp_status.get('neighbor_port'),
                        'new_status': log_lldp_status.get('neighbor_port')
                    })
        
        # 检查缺失LLDP端口
        for port in baseline_lldp:
            if port not in log_lldp:
                results['lldp_differences'].append({
                    'type': 'missing_lldp_port',
                    'port': port,
                    'status': baseline_lldp[port]
                })
        
        return results
    
    def build_device_mapping(self):
        """构建设备到基线文件的映射关系"""
        baseline_dates = self.get_baseline_dates()
        device_mapping = {}
        
        for date in baseline_dates:
            devices = self.get_devices_in_baseline(date)
            for device in devices:
                device_name = device["device_name"]
                if device_name not in device_mapping:
                    device_mapping[device_name] = {
                        "latest_baseline": date,
                        "history_baselines": [date],
                        "baseline_files": {date: device["file_name"]}
                    }
                else:
                    # 更新最新基线
                    if date > device_mapping[device_name]["latest_baseline"]:
                        device_mapping[device_name]["latest_baseline"] = date
                    # 添加到历史基线
                    if date not in device_mapping[device_name]["history_baselines"]:
                        device_mapping[device_name]["history_baselines"].append(date)
                    # 添加基线文件映射
                    device_mapping[device_name]["baseline_files"][date] = device["file_name"]
        
        return device_mapping
    
    def update_index(self):
        """更新基线索引"""
        baseline_dates = self.get_baseline_dates()
        if not baseline_dates:
            self.index["latest_baseline"] = None
            self.index["baseline_history"] = []
        else:
            self.index["latest_baseline"] = baseline_dates[-1]
            self.index["baseline_history"] = baseline_dates
        
        # 更新设备信息
        self.index["devices"] = {}
        for date in baseline_dates:
            devices = self.get_devices_in_baseline(date)
            for device in devices:
                device_name = device["device_name"]
                if device_name not in self.index["devices"]:
                    self.index["devices"][device_name] = {
                        "latest_log": date,
                        "history": [date]
                    }
                else:
                    if date > self.index["devices"][device_name]["latest_log"]:
                        self.index["devices"][device_name]["latest_log"] = date
                    if date not in self.index["devices"][device_name]["history"]:
                        self.index["devices"][device_name]["history"].append(date)
        
        self.save_index()

def generate_comparison_report(comparison_results):
    """生成可读性强的对比报告"""
    report = []
    report.append("="*60)
    report.append(f"设备日志对比报告 - {comparison_results['device_name']}")
    report.append("="*60)
    report.append(f"最新基线: {comparison_results['latest_baseline']}")
    report.append("")
    
    # 端口状态对比
    port_diff = comparison_results['comparison']['port_differences']
    if port_diff:
        report.append("1. 端口状态差异:")
        report.append("-"*40)
        for diff in port_diff:
            if diff['type'] == 'new_port':
                report.append(f"  + 新增端口: {diff['port']}")
                report.append(f"     状态: {diff['status']}")
            elif diff['type'] == 'missing_port':
                report.append(f"  - 缺失端口: {diff['port']}")
                report.append(f"     基线状态: {diff['status']}")
            elif diff['type'] == 'admin_status_change':
                report.append(f"  * 管理状态变化: {diff['port']}")
                report.append(f"     旧状态: {diff['old_status']}")
                report.append(f"     新状态: {diff['new_status']}")
            elif diff['type'] == 'line_status_change':
                report.append(f"  * 链路状态变化: {diff['port']}")
                report.append(f"     旧状态: {diff['old_status']}")
                report.append(f"     新状态: {diff['new_status']}")
        report.append("")
    else:
        report.append("1. 端口状态: 无差异 ✓")
        report.append("")
    
    # STP状态对比
    stp_diff = comparison_results['comparison']['stp_differences']
    if stp_diff:
        report.append("2. STP状态差异:")
        report.append("-"*40)
        for diff in stp_diff:
            if diff['type'] == 'new_stp_port':
                report.append(f"  + 新增STP端口: {diff['port']}")
                report.append(f"     状态: {diff['status']}")
            elif diff['type'] == 'missing_stp_port':
                report.append(f"  - 缺失STP端口: {diff['port']}")
                report.append(f"     基线状态: {diff['status']}")
            elif diff['type'] == 'stp_state_change':
                report.append(f"  * STP状态变化: {diff['port']}")
                report.append(f"     旧状态: {diff['old_status']}")
                report.append(f"     新状态: {diff['new_status']}")
            elif diff['type'] == 'stp_role_change':
                report.append(f"  * STP角色变化: {diff['port']}")
                report.append(f"     旧角色: {diff['old_status']}")
                report.append(f"     新角色: {diff['new_status']}")
        report.append("")
    else:
        report.append("2. STP状态: 无差异 ✓")
        report.append("")
    
    # LLDP状态对比
    lldp_diff = comparison_results['comparison']['lldp_differences']
    if lldp_diff:
        report.append("3. LLDP状态差异:")
        report.append("-"*40)
        for diff in lldp_diff:
            if diff['type'] == 'new_lldp_port':
                report.append(f"  + 新增LLDP端口: {diff['port']}")
                report.append(f"     状态: {diff['status']}")
            elif diff['type'] == 'missing_lldp_port':
                report.append(f"  - 缺失LLDP端口: {diff['port']}")
                report.append(f"     基线状态: {diff['status']}")
            elif diff['type'] == 'lldp_neighbor_presence_change':
                report.append(f"  * LLDP邻居存在性变化: {diff['port']}")
                report.append(f"     旧状态: {diff['old_status']}")
                report.append(f"     新状态: {diff['new_status']}")
            elif diff['type'] == 'lldp_neighbor_dev_change':
                report.append(f"  * LLDP邻居设备变化: {diff['port']}")
                report.append(f"     旧邻居: {diff['old_status']}")
                report.append(f"     新邻居: {diff['new_status']}")
            elif diff['type'] == 'lldp_neighbor_port_change':
                report.append(f"  * LLDP邻居端口变化: {diff['port']}")
                report.append(f"     旧端口: {diff['old_status']}")
                report.append(f"     新端口: {diff['new_status']}")
        report.append("")
    else:
        report.append("3. LLDP状态: 无差异 ✓")
        report.append("")
    
    # 统计信息
    total_diff = len(port_diff) + len(stp_diff) + len(lldp_diff)
    if total_diff == 0:
        report.append("4. 对比状态: 完全一致 ✓")
    else:
        report.append(f"4. 对比状态: 发现 {total_diff} 个差异 ✗")
    
    report.append("="*60)
    
    return "\n".join(report)

# ==============================================
# 主程序入口
# ==============================================

def main():
    """主程序"""
    import argparse
    
    base_dir = get_base_dir()
    default_baseline = os.path.join(base_dir, 'baseline')
    default_logs = os.path.join(base_dir, 'logs')
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="端口状态基线一致性检查工具")
    parser.add_argument('--baseline-dir', type=str, default=default_baseline,
                        help=f'指定基线文件夹路径 (默认: baseline)')
    parser.add_argument('--mode', type=str, default='compare', choices=['consistency', 'index', 'compare'],
                        help='运行模式: consistency(一致性检查), index(更新索引), compare(日志对比) (默认: compare)')
    parser.add_argument('--log-dir', type=str, default=default_logs,
                        help=f'指定日志文件夹路径 (默认: logs)')
    parser.add_argument('--quiet', action='store_true',
                        help='静默模式，只输出关键信息')
    parser.add_argument('--verbose', action='store_true',
                        help='详细模式，输出更多调试信息')
    parser.add_argument('--save-report', action='store_true',
                        help='保存报告到文件 (默认: 不保存)')
    parser.add_argument('--test', action='store_true',
                        help='测试模式，使用示例数据进行测试')
    
    args = parser.parse_args()
    
    # 初始化基线管理器
    if not os.path.exists(args.baseline_dir):
        try:
            os.makedirs(args.baseline_dir)
            if args.verbose:
                print(f"已创建基线目录: {args.baseline_dir}")
        except Exception as e:
            print(f"创建基线目录失败: {e}")
            
    baseline_manager = BaselineManager(args.baseline_dir)
    
    if args.mode == 'index':
        # 仅更新索引模式
        if args.verbose:
            print(f"更新基线索引...")
        baseline_manager.update_index()
        if not args.quiet:
            print("基线索引更新完成")
        sys.exit(0)
    
    # 其他模式都先进行基线一致性检查
    if not args.quiet:
        print("开始执行基线一致性检查...")
    
    # 检查基线一致性
    consistency_results = baseline_manager.compare_baseline_consistency()
    
    # 检查基线一致性状态
    if 'status' in consistency_results and consistency_results['status'] == 'info':
        print(f"错误：{consistency_results['message']}")
        sys.exit(1)
    
    # 统计基线问题数量
    total_issues = len(consistency_results['consistency_issues']) + len(consistency_results['missing_devices'])
    
    # 显示基线一致性状态
    if not args.quiet:
        if total_issues == 0:
            print("基线一致性状态: 全部一致 ✓")
        else:
            print(f"基线一致性状态: 发现 {total_issues} 个问题 ✗")
        print()
    
    # 如果基线有问题，询问用户是否打印详细信息
    if total_issues > 0:
        # 生成基线一致性报告
        report = baseline_manager.generate_consistency_report(consistency_results, verbose=args.verbose)
        report_lines = report.split('\n')
        
        # 询问用户是否打印基线详细信息
        try:
            print_details = input("基线检查发现问题，是否打印详细信息？(y/n，默认n): ").strip().lower()
            if print_details == 'y':
                print(report)
                print()
        except EOFError:
            # 非交互式环境下，默认不打印详细信息
            pass
    
    # 根据模式执行不同的逻辑
    if args.mode == 'compare':
        # 日志对比模式
        # 如果基线有问题，询问用户是否继续进行日志对比
        if total_issues > 0:
            try:
                continue_compare = input("是否继续进行日志对比？(y/n，默认y): ").strip().lower()
                if continue_compare == 'n':
                    print("已取消日志对比")
                    sys.exit(1)
            except EOFError:
                # 非交互式环境下，默认继续
                pass
        
        # 基线没问题或用户选择继续，执行索引更新
        if args.verbose:
            print(f"更新基线索引...")
        baseline_manager.update_index()
        
        # 2. 获取日志文件夹下的最新日志子文件夹
        import glob
        log_dirs = glob.glob(os.path.join(args.log_dir, '*'))
        log_dirs = [d for d in log_dirs if os.path.isdir(d)]
        if not log_dirs:
            print(f"错误：{args.log_dir} 文件夹下没有日志子文件夹")
            sys.exit(1)
        
        latest_log_dir = sorted(log_dirs)[-1]
        
        # 3. 获取日志文件
        log_files = glob.glob(os.path.join(latest_log_dir, "*.log"))
        if not log_files:
            print(f"错误：{latest_log_dir} 文件夹下没有日志文件")
            sys.exit(1)
        
        # 4. 遍历日志文件，与基线进行对比
        if not args.quiet:
            print(f"开始对比日志文件与基线...")
            print(f"最新日志文件夹: {latest_log_dir}")
            print(f"日志文件数量: {len(log_files)}")
            print()
        
        all_reports = []
        total_differences = 0
        
        for log_file in log_files:
            if not args.quiet:
                print(f"=== 对比日志文件: {log_file} ===")
            comparison_results = baseline_manager.compare_with_baseline(log_file)
            
            if comparison_results['status'] == 'success':
                # 生成对比报告
                report = generate_comparison_report(comparison_results)
                if not args.quiet:
                    print(report)
                    print()
                
                all_reports.append(report)
                
                # 统计差异数量
                comparison = comparison_results['comparison']
                diff_count = len(comparison['port_differences']) + len(comparison['stp_differences']) + len(comparison['lldp_differences'])
                total_differences += diff_count
            else:
                error_msg = f"错误：{comparison_results['message']}"
                if not args.quiet:
                    print(error_msg)
                    print()
                all_reports.append(error_msg)
        
        # 5. 保存综合报告到文件
        report_file = os.path.join(baseline_manager.baseline_root, f"comparison_report_{time.strftime('%Y%m%d_%H%M%S')}.txt")
        with open(report_file, "w", encoding="utf-8") as f:
            f.write("\n\n".join(all_reports))
        
        if not args.quiet:
            print(f"\n综合报告已保存到: {report_file}")
            print(f"总差异数量: {total_differences}")
        
        if total_differences > 0:
            sys.exit(1)  # 非零退出码表示有差异
        else:
            sys.exit(0)  # 零退出码表示无差异
    else:
        # 一致性检查模式
        # 基线没问题，执行索引更新
        if total_issues == 0:
            if args.verbose:
                print(f"更新基线索引...")
            baseline_manager.update_index()
        
        # 生成报告
        report = baseline_manager.generate_consistency_report(consistency_results, verbose=args.verbose)
        
        # 打印报告
        if not args.quiet:
            # 默认只打印关键信息（前6行和最后一行）
            report_lines = report.split('\n')
            if args.verbose:
                # 详细模式，打印完整报告
                print(report)
            else:
                # 非详细模式，只打印关键信息
                # 打印报告头（前6行）
                for line in report_lines[:6]:
                    print(line)
                
                # 统计问题数量
                if total_issues > 0:
                    # 有问题时，询问用户是否打印详细信息
                    try:
                        user_input = input("\n是否打印详细信息？(y/n，默认n): ").strip().lower()
                        if user_input == 'y':
                            # 打印所有报告
                            print('\n'.join(report_lines[6:]))
                        else:
                            # 只打印一致性状态
                            print(report_lines[-1])
                    except EOFError:
                        # 非交互式环境下，默认不打印详细信息
                        print(report_lines[-1])
                else:
                    # 没有问题时，只打印最后一行
                    print(report_lines[-1])
        
        # 保存报告到文件（只有当用户指定--save-report参数时）
        if args.save_report:
            report_file = os.path.join(baseline_manager.baseline_root, f"consistency_report_{time.strftime('%Y%m%d_%H%M%S')}.txt")
            with open(report_file, "w", encoding="utf-8") as f:
                f.write(report)
            if not args.quiet:
                print(f"\n报告已保存到: {report_file}")
        
        # 检查是否有一致性问题
        if total_issues > 0:
            if not args.quiet:
                print(f"\n发现 {total_issues} 个问题，请检查并修复！")
            sys.exit(1)  # 非零退出码表示有问题
        else:
            if not args.quiet:
                print("\n所有基线一致，状态正常！")
            sys.exit(0)  # 零退出码表示正常

if __name__ == "__main__":
    main()