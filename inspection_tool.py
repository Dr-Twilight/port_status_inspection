#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

# ==============================================
# 系统模块导入区
# ==============================================
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
from typing import List, Dict, Tuple
import os
import re
import sys
import time
import getpass
import threading
from io import BytesIO
import logging  # 引入日志模块
import traceback  # 引入traceback模块，用于异常信息的详细记录
import math   #NaN 检测需要


# ==============================================
# 第三方库导入区
# ==============================================
import msoffcrypto  # Excel文件解密库
import pandas as pd  # 数据处理库
from netmiko import ConnectHandler  # 网络设备连接库
try:
    from netmiko import exceptions  # Netmiko 4.x
except ImportError:
    import netmiko.ssh_exception as exceptions  # Netmiko 3.x 兼容


# ==============================================
# 编码修复区（解决特定环境下的idna编码问题）
# ==============================================
import idna
import codecs
import encodings.idna
# 注册自定义编码处理器，修复idna编码冲突
codecs.register(lambda name: encodings.idna.getregentry()
                if name == 'idna' else None)


# ==============================================
# 自定义异常定义
# ==============================================
class PasswordRequiredError(Exception):
    """
    文件受密码保护时抛出的异常
    用于明确区分密码缺失与其他文件读取错误
    """
    pass


# ==============================================
# 全局配置与路径处理
# ==============================================

# 每台设备最大巡检总时长（秒）
INSPECTION_TASK_TIMEOUT = 600
# 单条命令最大超时（秒）
INSPECTION_CMD_TIMEOUT = 10
# 默认超时时间
DEFAULT_TIMEOUT = INSPECTION_CMD_TIMEOUT
# 定义长超时时间（如3倍默认超时，最大240秒）
LONG_TIMEOUT = max(INSPECTION_CMD_TIMEOUT * 3, 240)
# 分页限制配置
DEFAULT_MAX_PAGE = 200          # 默认分页上限
BIG_OUTPUT_MAX_PAGE = DEFAULT_MAX_PAGE * 3  # 大输出命令分页上限（默认值的3倍）
# 分页保护机制：重复页最大次数，防止死循环
MAX_REPEAT_PAGE = 5

# 定义无回显命令集合（执行后通常无输出，需特殊处理）
NO_OUTPUT_CMDS = {
    "sys", "enable", "user-inter con 0", "quit",
    "undo screen-length", "screen-length disable",
    "screen-length enable", "screen-length 0",
    "screen-length 0 temporary"
}
# 定义输出巨大的命令集合
BIG_OUTPUT_CMDS = {
    "display ospf routing",
    "display ip routing-table statistics",
    "display ip routing-table",
    "display current-configuration",
    "display interface",
    "display stp",
    "display mac-address",
    "display device manuinfo",
    "display elabel"
}
# 定义分页符模式（用于处理输出分页的情况）
PAGINATION_PATTERNS = [
    "---- More ----",     # H3C、华为常见分页符
    "  ---- More ----  ",  # 华为设备变体（前后有空格）
    "---- More ----  ",   # 华为设备变体（后有空格）
    "  ---- More ----",   # 华为设备变体（前有空格）
    "--More--",           # H3C、Cisco
    "<--- More --->",     # 某些H3C设备
    "<Press ENTER to continue>",  # 华为设备
]
# 定义错误关键词（用于识别异常输出）
ERROR_KEYWORDS = [
        '连接失败', '超时异常', '无法连接', '连接断开', 'Socket is closed', '不可达',
        'Permission denied', '权限不足', '未授权', '拒绝访问',
        '不兼容', '执行异常', '无输出', '分页死循环', '未生效',
        '认证失败', 'login failed', '用户名或密码错误',
        '未进入系统视图'
    ]

# ==============================================
# 路径配置模块
# ==============================================

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
# 基础路径配置
# ==============================================

# 注意：顺序不可调换！
# 1.时间戳配置（全局统一时间基准）
RUN_START_TIME = time.localtime()  # 程序启动时的本地时间
LOCAL_TIME = time.strftime('%Y.%m.%d', RUN_START_TIME)  # 格式化日期（用于日志命名）
FILE_DATE = time.strftime('%Y-%m-%d', RUN_START_TIME)  # 用于文件名的日期格式（年-月-日）

# 2.信息文件路径
SCRIPT_DIR = get_base_dir()  # 脚本根目录
LOG_DIR = os.path.join(SCRIPT_DIR, 'logs')  # 日志存储目录
os.makedirs(LOG_DIR, exist_ok=True)  # 确保日志目录存在，不存在则创建

# 3.定义日志路径
LOG_DATE_DIR = os.path.join(LOG_DIR, LOCAL_TIME)  # logs/2025.06.09/
os.makedirs(LOG_DATE_DIR, exist_ok=True)    # 确保日志目录存在

# 4.获取用户输入的info文件名（默认为info_port.xlsx）
FILENAME = input(f"\n请输入info文件名（默认为 info_port.xlsx）：") or "info_port.xlsx"
INFO_PATH = os.path.join(SCRIPT_DIR, FILENAME)  # 拼接info文件完整路径



# ==============================================
# 日志配置模块与统一记录函数
# ==============================================

# —— 降噪配置：抑制 netmiko/paramiko 在通道已关闭时打印的 traceback ——
for name in ("netmiko", "paramiko", "paramiko.transport", "paramiko.channel"):
    lg = logging.getLogger(name)
    lg.setLevel(logging.WARNING)   # 可按需用 ERROR/CRITICAL
    lg.propagate = False           # 防止冒泡到根 logger 再打印

# 线程安全配置
LOCK = threading.RLock()  # 全局线程锁，防止多线程输出混乱,使用递归锁以避免死锁
# 统一日志记录函数（同时输出到控制台和01log.log）
ERROR_LOG_FILE = os.path.join(LOG_DIR, '01log.log') #输出到logs下和LOG_DATE_DIR同一层
# 启动时清空01log（在 logging.basicConfig 前执行）
with LOCK:
    try:
        if os.path.exists(ERROR_LOG_FILE):
            print(f"[日志初始化] 文件存在，删除旧日志：{ERROR_LOG_FILE}")
            os.remove(ERROR_LOG_FILE)
        else:
            print(f"[日志初始化] {ERROR_LOG_FILE} 不存在，无需删除。")
    except Exception as e:
        print(f"[日志初始化] 删除失败：{e}")
        try:
            with open(ERROR_LOG_FILE, 'w', encoding='utf-8'):
                pass
            print("[日志初始化] 无法删除，已清空01log.log内容。")
        except Exception as inner_e:
            print(f"[日志初始化] 无法清空异常日志文件: {inner_e}")
# 日志系统配置（必须只调用一次）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(ERROR_LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# 日志统一封装函数，兼容原有 log_message 调用方式
def log_message(msg: str, level='info'):
    with LOCK:
        try:
            if level == 'info':
                logging.info(msg)
            elif level == 'warning':
                logging.warning(msg)
            elif level == 'error':
                logging.error(msg)
            elif level == 'critical':
                logging.critical(msg)
            else:
                logging.debug(msg)
        except Exception as e:
            print(f"[日志记录异常] 记录日志失败: {e}")


# ==============================================
# 数据读取模块
# ==============================================

# 数据清理函数
def _safe_str(x) -> str:
    """把 None/NaN/非字符串安全转换为字符串；NaN 返回空串。"""
    if x is None:
        return ""
    if isinstance(x, float):
        try:
            if math.isnan(x): #识别 pandas 的 NaN
                return ""
        except Exception:
            pass
        return str(x) #普通 float 显式转字符串，避免 .strip 报错
    return x if isinstance(x, str) else str(x)

# ✅ 修改后的强力字符串清理函数
def _strip_or_empty(x) -> str:
    """
    字符串清洗：
    - 去掉 Excel 遗留的 `_x000d_`
    - 去掉所有 \r \n \t 控制符
    - 去掉首尾空格
    """
    if not isinstance(x, str):
        return ""
    cleaned = x.replace("_x000d_", "")   # 移除 Excel 隐藏回车
    cleaned = re.sub(r"[\r\n\t]", "", cleaned)  # 移除控制符
    return cleaned.strip()

# 列表/元组清理函数
def _clean_list_to_str(seq):
    """把列表/元组中所有元素安全转字符串并 strip，过滤空值。"""
    if not isinstance(seq, (list, tuple)):
        seq = [seq]
    out = []
    for v in seq:
        sv = _strip_or_empty(_safe_str(v))
        if sv:
            out.append(sv)
    return out


# 判断info文件是否被加密，使用不同的读取方式
def read_info() -> Tuple[List[Dict], Dict[str, List]]:
    if is_encrypted(INFO_PATH):
        return read_encrypted_file(INFO_PATH)  # 读取被加密info文件
    else:
        return read_unencrypted_file(INFO_PATH)  # 读取未加密info文件

# 检测info文件是否被加密
def is_encrypted(info_file: str) -> bool:
    try:
        with open(info_file, "rb") as f:
            return msoffcrypto.OfficeFile(f).is_encrypted()  # 检测info文件是否被加密
    except Exception:
        return False


# 读取被加密info文件
def read_encrypted_file(info_file: str, max_retry: int = 3) -> pd.DataFrame:
    retry_count = 0  # 初始化重试计数器，用于记录用户尝试输入密码的次数
    while retry_count < max_retry:  # 当重试次数小于最大允许重试次数时，继续循环
        try:
            # 提示用户输入密码，隐式输入。如果用户直接按Enter键，password将为None
            password = getpass.getpass("\n info文件被加密，请输入密码：") or None
            if not password:  # 如果用户没有输入密码
                raise PasswordRequiredError(
                    "文件受密码保护，必须提供密码！")  # 抛出自定义异常，提示用户必须提供密码

            # 解密文件
            decrypted_data = BytesIO()  # 创建一个BytesIO对象，用于在内存中存储解密后的文件内容
            # BytesIO是一个内存中的二进制流，可以像文件一样进行读写操作
            with open(info_file, "rb") as f:  # 以二进制只读模式打开加密的info文件
                # 使用msoffcrypto库创建一个OfficeFile对象，表示加密的Office文件
                office_file = msoffcrypto.OfficeFile(f)
                office_file.load_key(password=password)  # 使用用户提供的密码加载解密密钥
                # 解密文件内容，并将解密后的数据写入decrypted_data对象中
                office_file.decrypt(decrypted_data)
            decrypted_data.seek(0)  # 将decrypted_data的指针重置到起始位置，以便后续读取操作
            # 由于解密后的数据已经写入decrypted_data，需要将指针重置到开头，以便后续读取

            # 读取解密后的文件
            devices_dataframe = pd.read_excel(
                decrypted_data, sheet_name=0, dtype=str, keep_default_na=False)
            cmds_dataframe = pd.read_excel(
                decrypted_data, sheet_name=1, dtype=str,keep_default_na=False)
                # === 新增：对两张表做“去空白 + 非字符串置空串”的清洗 ===
            devices_dataframe = devices_dataframe.applymap(_strip_or_empty)
            cmds_dataframe    = cmds_dataframe.applymap(_strip_or_empty)

        except FileNotFoundError:  # 如果没有配置info文件或info文件名错误
            print(f'\n没有找到info文件！\n')  # 提示用户没有找到info文件或info文件名错误
            # input('输入Enter退出！')  # 提示用户按Enter键退出，这里打包后的程序或非交互环境中，这个输入等待会导致程序进入死循环，因为没有用户会响应这个输入请求。
            sys.exit(1)  # 异常退出
        except ValueError:  # 捕获异常信息
            print(f'\ninfo文件缺失子表格信息！\n')  # 代表info文件缺失子表格信息
            input('输入Enter退出！')  # 提示用户按Enter键退出
            sys.exit(1)  # 异常退出
        except (msoffcrypto.exceptions.InvalidKeyError, PasswordRequiredError) as e:
            retry_count += 1
            if retry_count < max_retry:
                print(f"\n密码错误，请重新输入！（剩余尝试次数：{max_retry - retry_count}）")
            else:
                input("\n超过最大尝试次数，输入Enter退出！")
                sys.exit(1)
        except Exception as e:
            print(f"\n解密失败：{str(e)}")
            sys.exit(1)
        else:
            devices_dict = devices_dataframe.to_dict('records')  # 将DataFrame转换成字典
            # "records"参数规定外层为列表，内层以列标题为key，以此列的行内容为value的字典
            # 若有多列，代表字典内有多个key:value对；若有多行，每行为一个字典

            cmds_dict = cmds_dataframe.to_dict('list')  # 将DataFrame转换成字典
            # "list"参数规定外层为字典，列标题为key，列下所有行内容以list形式为value的字典
            # 若有多列，代表字典内有多个key:value对

            # === 对 cmds_dict 的每一列进行二次清洗（把 None/NaN 变成 ""，strip）===
            for k, lst in list(cmds_dict.items()):
                if not isinstance(lst, list):
                    lst = [lst]
                lst = [_strip_or_empty(_safe_str(x)) for x in lst]
                cmds_dict[k] = lst

            return devices_dict, cmds_dict


# 读取未加密info文件
def read_unencrypted_file(info_file: str) -> pd.DataFrame:
    try:
        devices_dataframe = pd.read_excel(info_file, sheet_name=0, dtype=str, keep_default_na=False)
        cmds_dataframe = pd.read_excel(info_file, sheet_name=1, dtype=str, keep_default_na=False)
        # === info表统一清洗 ===
        # 替换applymap为apply + lambda + map
        devices_dataframe = devices_dataframe.apply(lambda col: col.map(_strip_or_empty))
        cmds_dataframe    = cmds_dataframe.apply(lambda col: col.map(_strip_or_empty))

    except FileNotFoundError:  # 如果没有配置info文件或info文件名错误
        print(f'\n没有找到info文件！\n')  # 代表没有找到info文件或info文件名错误
        input('输入Enter退出！')  # 提示用户按Enter键退出
        sys.exit(1)  # 异常退出
    except ValueError:  # 捕获异常信息
        print(f'\ninfo文件缺失子表格信息！\n')  # 代表info文件缺失子表格信息
        input('输入Enter退出！')  # 提示用户按Enter键退出
        sys.exit(1)  # 异常退出
    else:
        devices_dict = devices_dataframe.to_dict('records')  # 将DataFrame转换成字典
        # "records"参数规定外层为列表，内层以列标题为key，以此列的行内容为value的字典
        # 若有多列，代表字典内有多个key:value对；若有多行，每行为一个字典

        cmds_dict = cmds_dataframe.to_dict('list')  # 将DataFrame转换成字典
        # "list"参数规定外层为字典，列标题为key，列下所有行内容以list形式为value的字典
        # 若有多列，代表字典内有多个key:value对

        # === 对 cmds_dict 的每一列进行二次清洗（把 None/NaN 变成 ""，strip）===
        for k, lst in list(cmds_dict.items()):
            if not isinstance(lst, list):
                lst = [lst]
            lst = [_strip_or_empty(_safe_str(x)) for x in lst]
            cmds_dict[k] = lst

        return devices_dict, cmds_dict


# 自动分页处理函数
def handle_pagination(
        ssh,
        cmd,
        timeout_per_cmd,
        max_page=DEFAULT_MAX_PAGE,
        enable_show_output='n'):
    """
    自动处理分页回显，兼容无输出命令和大输出命令。
    - 利用全局变量
    NO_OUTPUT_CMDS 无输出命令列表
    BIG_OUTPUT_CMDS 大输出命令列表
    PAGINATION_PATTERNS 分页符列表
    LONG_TIMEOUT 长超时时间
    """
    # 增强命令清洗
    cmd = _safe_str(cmd)
    cmd_clean = _strip_or_empty(cmd)
    if not cmd_clean:
        return "跳过空命令"
    # 1. 无输出命令特殊处理
    if cmd_clean in NO_OUTPUT_CMDS:
        # 增强日志记录
        if enable_show_output == 'y':
            with LOCK:
                log_message(f"[DEBUG] 设备 {ssh.host} 执行无输出命令：{cmd_clean}")
        show = ssh.send_command_timing(cmd, read_timeout=timeout_per_cmd)
        # === 修改：对 show 做一次安全规整再判断 ===
        s_show = _safe_str(show)
        
        # 新增：错误信息检测
        error_patterns = [
            "Permission denied",
            "Unrecognized command",
            "% Unrecognized command",
            "Error: Unrecognized command",
            "invalid input",
            "Invalid command"
        ]
        
        has_error = any(pattern in s_show for pattern in error_patterns)
        
        # 新增：screen-length命令特殊处理
        if "screen-length" in cmd_clean and has_error:
            # 尝试其他screen-length相关命令
            alternative_cmds = [
                "screen-length 0",
                "screen-length 0 temporary",
                "undo screen-length",
                "screen-length enable"
            ]
            
            for alt_cmd in alternative_cmds:
                if alt_cmd != cmd_clean:  # 避免重复尝试相同命令
                    if enable_show_output == 'y':
                        with LOCK:
                            log_message(f"[DEBUG] 设备 {ssh.host} 命令 {cmd_clean} 执行失败，尝试替代命令：{alt_cmd}")
                    alt_show = ssh.send_command_timing(alt_cmd, read_timeout=timeout_per_cmd)
                    alt_s_show = _safe_str(alt_show)
                    # 检查替代命令是否执行成功
                    if not any(pattern in alt_s_show for pattern in error_patterns):
                        if enable_show_output == 'y':
                            with LOCK:
                                log_message(f"[DEBUG] 设备 {ssh.host} 替代命令 {alt_cmd} 执行成功")
                        return f"命令 {_safe_str(cmd)} 执行失败，已尝试替代命令 {alt_cmd}：{alt_s_show.strip()}"
        
        # 原有错误处理
        if has_error:
            if enable_show_output == 'y':
                with LOCK:
                    log_message(f"[DEBUG] 设备 {ssh.host} 命令 {cmd_clean} 执行失败：{s_show.strip()}")
            return f"命令 {_safe_str(cmd)} 执行失败：{s_show.strip()}"
        
        if s_show.strip() == "":
            if enable_show_output == 'y':
                with LOCK:
                    log_message(f"[DEBUG] 设备 {ssh.host} 命令 {cmd_clean} 执行成功，无输出")
            return f"命令 {_safe_str(cmd)} 执行完毕，无输出。"
        elif s_show.strip() == cmd:
            if enable_show_output == 'y':
                with LOCK:
                    log_message(f"[DEBUG] 设备 {ssh.host} 命令 {cmd_clean} 已发送，可能无回显")
            return f"命令 {_safe_str(cmd)} 已发送，但可能无回显或未生效。"
        else:
            if enable_show_output == 'y':
                with LOCK:
                    log_message(f"[DEBUG] 设备 {ssh.host} 命令 {cmd_clean} 返回结果：{s_show.strip()}")
            return show

    # 2. 输出巨大的命令，自动放大max_page和超时
    if cmd_clean in BIG_OUTPUT_CMDS:
        # 增强日志记录
        if enable_show_output == 'y':
            with LOCK:
                log_message(f"[DEBUG] 设备 {ssh.host} 执行大输出命令：{cmd_clean}")
        max_page = max(max_page, BIG_OUTPUT_MAX_PAGE)   # 大输出命令最大页数设为3倍，最大不超过LONG_TIMEOUT
        # 大输出命令超时时间设为3倍，最大不超过LONG_TIMEOUT
        timeout_per_cmd = min(timeout_per_cmd * 3, LONG_TIMEOUT)
        if enable_show_output == 'y':
            with LOCK:
                log_message(f"[DEBUG] 大输出命令调整：max_page={max_page}, timeout={timeout_per_cmd}")

    # 3. 标准分页处理
    show = ssh.send_command_timing(cmd, read_timeout=timeout_per_cmd)
    # 快速判定不可识别命令
    # === 修改：用 _safe_str 包一层，避免后续 in 判断打到 float ===
    s_show = _safe_str(show)
    if ("Unrecognized command" in s_show or
            "Error: Unrecognized command found at '^' position." in s_show):
        return show

    page_count = 0  # 分页计数器
    prev_output = ""    # 上一页输出内容
    repeat_count = 0    # 重复页计数器

    pagination_start_time = time.time() # 分页开始时间

    while True:
        # 分页过程中如遇到错误命令立即break
        # === 修改：循环内也使用 s_show 做判断 ===
        s_show = _safe_str(show)

        # 1.检测不可识别的命令
        if ("Unrecognized command" in s_show or
                "Error: Unrecognized command found at '^' position." in s_show):
            # 分页调试日志
            if enable_show_output == 'y':
                with LOCK:
                    log_message(f"[DEBUG][分页调试] 不可识别的命令‘{s_show}’，已强制中止。")
            break

        # 2.死循环保护：回显未变化时计数
        # 如果连续翻页内容无变化，则判定死循环
        if s_show.strip() == prev_output.strip():
            repeat_count += 1
        else:
            repeat_count = 0
            prev_output = s_show
        # 分页调试日志
        if enable_show_output == 'y':
            with LOCK:
                log_message(f"[DEBUG][分页调试] 当前页内容长度：{len(s_show.strip())}，重复计数：{repeat_count}")

        # 判定重复页次数，超过最大次数则强制中止
        # 为大输出命令调整重复页阈值，允许更多重复页
        current_max_repeat = MAX_REPEAT_PAGE * 2 if cmd_clean in BIG_OUTPUT_CMDS else MAX_REPEAT_PAGE
        if repeat_count >= current_max_repeat:
            show += "\n[警告] 多次翻页后内容无变化，可能陷入分页死循环，已强制中止。\n"
            break

        # 3.超时保护
        if time.time() -  pagination_start_time > timeout_per_cmd:
            show += "\n[警告] 分页命令执行超时，已强制中止。\n"
            break

        # 4.正常分页符检测
        found = False
        for pattern in PAGINATION_PATTERNS:
            p = _strip_or_empty(_safe_str(pattern))
            if not p:
                continue
            # 宽松匹配，允许分页符前后有其他字符
            if p in s_show or p.lower() in s_show.lower():
                # 分页调试日志
                if enable_show_output == 'y':
                    with LOCK:  # 加锁同步控制台输出
                        log_message(f"[分页调试] 第{page_count}页，识别到分页符：{p}")
                found = True
                # 根据分页符自动翻页
                if p in ["---- More ----", "--More--", "<--- More --->", "  ---- More ----  ", "---- More ----  ", "  ---- More ----"]:
                    log_message(f"[分页] 设备 {_safe_str(ssh.host)} 命令 {_safe_str(cmd)} 遇到分页符 {repr(p)}，已发送空格翻页。")
                    show += ssh.send_command_timing(" ", read_timeout=timeout_per_cmd)
                elif p == "<Press ENTER to continue>":
                    log_message(f"[分页] 设备 {_safe_str(ssh.host)} 命令 {_safe_str(cmd)} 遇到分页符 {repr(p)}，已发送回车翻页。")
                    show += ssh.send_command_timing("\n", read_timeout=timeout_per_cmd)
                break
        page_count += 1
        if not found or page_count > max_page:
            break
    return show

# 检测Paramiko通道是否关闭，会影响ssh连接
def channel_closed(ssh) -> bool:
    """无副作用检测：不写通道，仅查看底层 Paramiko 通道是否已关闭。"""
    try:
        rc = getattr(ssh, "remote_conn", None)
        # Paramiko Channel 在关闭后 rc.closed == True
        return (rc is None) or getattr(rc, "closed", False)
    except Exception:
        return True


# 巡检主函数
def inspection(login_info, cmds_dict, enable_show_output):
    # 使用传入的设备登录信息和巡检命令登录设备并执行巡检
    # 若登录异常，生成01log文件记录错误信息
    inspection_start_time = time.time()  # 子线程执行计时起始点，用于计算执行耗时
    ssh = None         # 初始化SSH连接对象

    # 输出调试信息：idna模块路径和sys.path（当前已注释）
    # print(f"idna路径: {idna.__file__}")
    # print(f"sys.path: {sys.path}")


    try:  # 尝试登录设备
        # 记录连接尝试日志，包含超时时间信息
        log_message(
            f'设备 {login_info["host"]} 开始连接（超时时间 {login_info["conn_timeout"]} 秒）')
        # 连接调试日志
        if enable_show_output == 'y':
            with LOCK:  # 加锁同步控制台输出
                log_message(f"[DEBUG] 正在连接设备：{login_info['host']} ...")
        ssh = ConnectHandler(
            session_log=os.path.join(
                LOG_DATE_DIR, f"[{login_info['host']}]_[{FILE_DATE}].log"),  # 自动记录完整交互日志
            **login_info
        )


        # 仅当 secret 字段存在且非空时才执行 enable
        if login_info.get("secret"):
            ssh.enable()

    except Exception as ssh_error:  # 登录设备出现异常
        exception_name = type(ssh_error).__name__  # 获取异常类型名称

        # 根据不同异常类型生成针对性日志
        if exception_name == 'AttributeError':
            log_message(f'设备 {login_info["host"]} 缺少设备管理地址！', level='error')
        elif exception_name == 'NetmikoTimeoutException':
            if 'TCP timeout' in str(ssh_error) or 'Connection refused' in str(ssh_error):
                log_message(f'设备 {login_info["host"]} 未响应（可能未开机或网络不可达）', level='error')
            else:
                log_message(f'设备 {login_info["host"]} 管理地址或端口不可达！', level='error')
        elif exception_name == 'NetmikoAuthenticationException':
            log_message(f'设备 {login_info["host"]} 用户名或密码认证失败！', level='error')
        elif exception_name == 'ValueError':
            if login_info.get("secret"):  # secret存在，说明设备期望进入enable
                log_message(f'设备 {login_info["host"]} Enable密码认证失败！', level='error')
            else:
                log_message(
                    f'设备 {login_info.get("host", "未知设备")} 不需要Enable密码，已跳过。')
        elif exception_name == 'TimeoutError':
            log_message(f'设备 {login_info["host"]} Telnet连接超时！', level='error')
        elif exception_name == 'ReadTimeout':
            if login_info.get("secret"):
                log_message(
                    f'设备 {login_info["host"]} Enable密码认证失败！（ReadTimeout）', level='error')
            else:
                log_message(
                    f'设备 {login_info["host"]} 不需要Enable密码（ReadTimeout），已跳过。')
        elif exception_name == 'ConnectionRefusedError':
            log_message(f'设备 {login_info["host"]} 远程登录协议错误！', level='error')
        elif exception_name == 'TypeError':
            log_message(f'设备 {login_info["host"]} 登录信息格式异常，可能缺字段！', level='error')
        else:
            log_message(
                f'设备 {login_info["host"]} 未知错误！{type(ssh_error).__name__}: {str(ssh_error)}', level='error')
        return  # 登录失败直接返回

    else:  # 如果登录正常，开始执行巡检命令
        # 安全冗余检查：防止异常情况下ssh对象未正确创建
        if ssh is None:
            log_message(f"[异常保护] 设备 {login_info['host']} SSH 连接对象未建立，跳过巡检。", level='error')
            return

        # 获取设备真实主机名（通过SSH会话提示符解析）
        real_hostname = ssh.find_prompt().strip()

        # 加锁同步控制台输出，避免多线程打印混乱
        with LOCK:
            log_message(f'设备 {login_info["host"]} 正在巡检...')

        # 遍历当前设备类型对应的所有巡检命令
        # 获取命令列表，防止KeyError
        cmds = cmds_dict.get(login_info['device_type'])
        if not cmds:
            log_message(f"设备类型 {login_info['device_type']} 未配置命令，跳过设备 {login_info['host']}", level='error')
            return
        for cmd in cmds:
            # 增强命令验证
            cmd = _safe_str(cmd)
            cmd_clean = _strip_or_empty(cmd)
            if not cmd_clean:
                log_message(f"设备 {login_info['host']} 跳过空命令", level='debug')
                continue
            # 命令调试日志
            if enable_show_output == 'y':
                with LOCK:  # 加锁同步控制台输出
                    log_message(f"[DEBUG] 设备 {login_info['host']} 执行命令: {cmd_clean}")
            # 1. 每条命令前第一步，检查任务是否超时，超时立即终止并断开SSH
            elapsed = time.time() - inspection_start_time  # 计算当前命令执行耗时
            remaining = INSPECTION_TASK_TIMEOUT - elapsed  # 计算剩余时间
            # 超时检查
            if remaining <= 0:
                log_message(f'设备 {login_info["host"]} 巡检任务超时，已主动中止', level='error')
                # 确保SSH连接已断开
                if ssh is not None:
                    try:
                        ssh.disconnect()
                    except Exception:
                        pass
                return

            # 检查SSH连接是否仍然存活
            # 2. 无副作用检查：若通道已关闭，终止后续命令
            if channel_closed(ssh):
                log_message(f'设备 {login_info["host"]} SSH连接断开，终止后续命令。')
                break

            # 3. 计算本命令的最大超时时间（取剩余时间和单条命令最大超时的较小值）
            timeout_per_cmd = min(INSPECTION_CMD_TIMEOUT, remaining)
            try:
                # 4. 所有命令都统一用handle_pagination处理，包含自动分页和错误命令检测
                try:
                    show = handle_pagination(ssh, cmd, timeout_per_cmd, enable_show_output=enable_show_output)

                except Exception as e:
                    log_message(
                        f"设备 {login_info['host']} 分页处理异常，已禁用自动翻页并继续：{e}", level='error')
                    # —— 回退策略：不做自动翻页，直接拿一次基础输出 ——
                    try:
                        base_output = ssh.send_command_timing(
                            _safe_str(cmd), read_timeout=timeout_per_cmd
                        )
                    except Exception as e2:
                        log_message(
                            f"设备 {login_info['host']} 基础输出获取也失败：{e2}", level='error'
                        )
                        base_output = ""
                    show = base_output
                    # 命令执行完成调试日志
                    if enable_show_output == 'y':
                        with LOCK:  # 加锁同步控制台输出
                            log_message(f"[DEBUG] 设备 {login_info['host']} 命令执行完成: {cmd}")
                # —— 统一把 show 安全规整为字符串，再做后续判断 ——
                s_show = _safe_str(show)  # 确保show为字符串，防止后续操作出错

                # 5. 检查命令回显是否为不可识别命令，若是则记录日志并直接continue，不再多余等待
                if (
                    "Unrecognized command" in s_show or
                    "Error: Unrecognized command found at '^' position." in s_show
                ):
                    # 安全获取“最后一行”，避免空串 splitlines() 后取 [-1] 抛 IndexError
                    _trimmed = s_show.strip()
                    _lines = _trimmed.splitlines() if _trimmed else []
                    _last = _lines[-1] if _lines else _trimmed
                    log_message(
                        f"设备 {login_info['host']} 命令 {_safe_str(cmd)} 不兼容或错误：{_last}", level='warning'
                    )
                    continue  # 跳过当前命令，继续下一个

            # 捕获ssh命令执行异常并处理
            except OSError as e:
                # 6. quit 或连接被远端关闭时常见 socket closed，这里记录为info并break  [FIX-INDENT]
                if "Socket is closed" in str(e):
                    log_message(
                        f'设备 {login_info["host"]} 命令 {_safe_str(cmd)} 执行后连接已关闭（常见于quit或远端主动断开），后续命令跳过。'
                    )
                else:
                    log_message(
                        f'设备 {login_info["host"]} 命令 {_safe_str(cmd)} 执行异常: {type(e).__name__}: {str(e)}', level='error'
                    )
                break
            except exceptions.NetmikoTimeoutException as e:
                log_message(f'设备 {login_info["host"]} SSH超时异常: {str(e)}', level='error')
                break

            # 7. quit命令特殊处理：如quit后连接断开则break，否则继续后续命令
            if cmd.strip().lower() == "quit":
                if channel_closed(ssh):
                    log_message(f'设备 {login_info["host"]} quit命令后SSH连接断开（如预期），后续命令已跳过。')
                    break
                else:
                    log_message(f'设备 {login_info["host"]} quit命令后仍在用户模式，可继续后续命令。')

            # 8. 根据用户设置决定是否在控制台显示回显
            if enable_show_output == 'y':
                with LOCK:  # 加锁同步控制台输出
                    print(f'{real_hostname} {_safe_str(cmd)} 回显如下：\n{show}\n')

    finally:  # 无论登录成功与否，最终执行资源清理
        try:  # 只在SSH对象存在且连接活跃时断开连接,总的资源释放保护（包括ssh.is_alive()和ssh.disconnect()）
            if ssh is not None:
                try:
                    # 资源释放前调试日志
                    if enable_show_output == 'y':
                        with LOCK:  # 加锁同步控制台输出
                            log_message(f"[DEBUG] 设备 {login_info['host']} SSH连接即将关闭")
                    ssh.disconnect()
                except OSError as e:
                    if "Socket is closed" in str(e):
                        log_message(f"设备 {login_info['host']} SSH连接已被远程关闭。（释放资源时检测到socket已关闭，属预期，无需处理）")
                    else:
                        tb = traceback.format_exc()
                        log_message(f"设备 {login_info['host']} 断开连接失败: {str(e)}\n{tb}", level='error')
                except Exception as e:
                    tb = traceback.format_exc()
                    log_message(f"设备 {login_info['host']} 断开连接遇到异常: {str(e)}\n{tb}", level='error')
            # 资源释放日志,注意缩进位置
            inspection_end_time = time.time()
            log_message(f"设备 {login_info['host']} 巡检完成，耗时 {round(inspection_end_time - inspection_start_time, 2)} 秒")
            log_message(f"设备 {login_info['host']} SSH连接已关闭，任务资源已释放")
        except Exception as e:
            # 兜底：保护 finally 自身不因意外而中断
            tb = traceback.format_exc()
            log_message(f"设备 {login_info['host']} finally块内部出现异常: {e}\n{tb}", level='error')

# 自定义守护线程池执行器
# 继承自ThreadPoolExecutor，重写_worker方法确保所有工作线程为守护线程
# 解决主线程退出后子线程仍可能残留的问题

class DaemonThreadPoolExecutor(ThreadPoolExecutor):
    """自定义守护线程池，确保主线程退出时所有子线程随之终止"""

    def _worker(self, *args, **kwargs):
        thread = threading.current_thread()
        thread.daemon = True  # 设置为守护线程
        super()._worker(*args, **kwargs)


if __name__ == '__main__':
    # 主线程计时器main_start_time,计算巡检总耗时
    main_start_time = time.time()
    # 读取设备信息和命令配置
    devices_info, cmds_info = read_info()
    # 获取用户输入，是否显示实时命令输出（默认不显示）
    enable_show_output = input("是否显示实时命令输出？(y/n, 默认n): ").strip().lower() or 'n'

    log_message(f'\n''>>> 开始巡检 <<<''\n')
    log_message(f'\n' + '>' * 40 + '\n')


    # 线程池配置 - 动态调整大小
    # 1. 获取CPU核心数（处理可能为None的情况，默认使用4核心）
    # 2. 计算最大工作线程数：设备数量、CPU核心数*5、200的最小值
    #    确保线程池不会过度消耗系统资源
    cpu_count = os.cpu_count() or 4  # 处理 None 情况，默认使用 4 核心
    max_workers = min(len(devices_info), cpu_count * 5, 200)

    # 使用自定义守护线程池执行巡检任务
    # thread_name_prefix用于调试时识别线程来源
    with DaemonThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='DeviceInspect') as executor:
        futures = []
        # 遍历所有设备信息，提交巡检任务
        for device_info in devices_info:
            # 验证设备信息必填字段
            required_fields = ['device_type', 'host', 'ip', 'username','port']
            missing_fields = [f for f in required_fields if str(device_info.get(f)).strip() == '']
            if missing_fields:
                log_message(f"[跳过] 设备信息字段不完整（缺失: {', '.join(missing_fields)}），设备信息: {device_info}", level='warning')
                continue

            # 复制设备信息并添加连接超时配置
            updated_device_info = device_info.copy()
            updated_device_info["conn_timeout"] = 15  # 设置连接超时为15秒

            # 提交巡检任务到线程池
            # inspection: 实际执行巡检的函数
            # updated_device_info: 设备连接信息
            # cmds_info: 命令配置信息
            # enable_show_output: 是否显示实时输出标志
            future = executor.submit(
                inspection,
                updated_device_info,
                cmds_info,
                enable_show_output
            )
            # 存储future对象和对应的主机名，用于后续结果处理
            futures.append((future, device_info['host']))
            log_message(f"[线程池] 已提交任务: {device_info['host']}")

        # 处理所有任务结果
        for future, host in futures:
            try:
                # 获取任务结果，设置超时时间为INSPECTION_TASK_TIMEOUT秒+几秒冗余（如+5），避免边界误判
                future.result(timeout=INSPECTION_TASK_TIMEOUT+5)
            except FutureTimeout:
                # 理论上inspection内已经做了超时自我终止
                log_message(
                    f"设备 {host} 巡检任务超时（>{INSPECTION_TASK_TIMEOUT}秒），请检查inspection函数内部超时控制是否生效", level='warning')
            except Exception as e:
                tb = traceback.format_exc()
                log_message(f"设备 {host} 巡检任务异常: {str(e)}\n{tb}", level='error')

    # 统计错误设备数量
    try:
        error_devices = set()
        # 读取日志文件，提取错误设备信息
        with open(os.path.join(LOG_DIR, '01log.log'), 'r', encoding='utf-8') as log_file:
            for line in log_file:
                if ERROR_KEYWORDS and any(keyword in line for keyword in ERROR_KEYWORDS):
                    parts = line.split()
                    # 调整设备名提取逻辑以匹配实际日志格式
                    if len(parts) >= 5 and parts[3] == '设备':
                        error_devices.add(parts[4])
                    elif len(parts) >= 4 and parts[2].startswith('设备'):
                        error_devices.add(parts[3])
        file_lines = len(error_devices)
        #用 log_message 记录异常设备统计信息
        if file_lines > 0:
            log_message(f'共发现 {file_lines} 台设备存在异常：',level='warning')
            for dev in sorted(error_devices):
                log_message(f'- 异常设备：{dev}',level='warning')
        else:
            log_message('未发现异常设备。')
    except FileNotFoundError:
        file_lines = 0
        log_message('未找到日志文件，无法统计异常设备。',level='warning')

    # 主线程计时器main_end_time,计算巡检总耗时
    main_end_time = time.time()
    log_message(f'\n' + '<' * 40 + '\n')
    log_message(
        f'巡检完成，共巡检 {len(futures)} 台设备，{file_lines} 台异常，共用时 {round(main_end_time - main_start_time, 1)} 秒。\n')
    log_message(f"线程池已关闭，所有任务已完成")
    input('\n巡检完成，请按 任意键 退出程序...')

