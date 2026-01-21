#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import os

def main():
    # 检查是否作为Python解释器被调用（即有脚本参数）
    if len(sys.argv) > 1:
        # 获取要执行的脚本路径
        script_path = sys.argv[1]
        if os.path.exists(script_path):
            # 直接执行脚本
            import runpy
            runpy.run_path(script_path, run_name='__main__')
        else:
            print(f"Error: Script not found: {script_path}")
            sys.exit(1)
        return
    
    print("设备巡检与端口状态检查主程序开始执行...")
    
    # 获取当前脚本所在目录
    if getattr(sys, 'frozen', False):
        # 如果是打包后的exe，脚本文件位于临时目录中
        script_dir = sys._MEIPASS
    else:
        # 如果是直接运行脚本
        script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 定义要执行的脚本路径
    inspection_tool = os.path.join(script_dir, "inspection_tool.py")
    port_status_tool = os.path.join(script_dir, "port_status_inspection.py")
    
    # 1. 执行inspection_tool.py
    print("\n1. 开始执行设备巡检脚本...")
    try:
        # 直接使用runpy执行脚本，避免subprocess的问题
        import runpy
        runpy.run_path(inspection_tool, run_name='__main__')
        print(f"设备巡检脚本执行完成")
    except Exception as e:
        print(f"执行设备巡检脚本时发生错误: {e}")
        choice = input("\n是否继续执行端口状态检查脚本？(y/n): ")
        if choice.lower() != 'y':
            print("程序终止执行")
            return 1
    
    # 2. 执行port_status_inspection.py
    print("\n2. 开始执行端口状态检查脚本...")
    try:
        # 直接使用runpy执行脚本，避免subprocess的问题
        import runpy
        runpy.run_path(port_status_tool, run_name='__main__')
        print(f"端口状态检查脚本执行完成")
    except SystemExit as e:
        if e.code != 0:
            print(f"端口状态检查脚本异常退出 (代码 {e.code})")
    except Exception as e:
        print(f"执行端口状态检查脚本时发生错误: {e}")
    
    input("\n按回车键退出程序...")
    return 0

if __name__ == '__main__':
    sys.exit(main())