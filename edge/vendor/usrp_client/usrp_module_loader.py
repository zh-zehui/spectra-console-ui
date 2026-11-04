#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
USRP模块动态加载器
用于加载123_7.py中的ParallelSyncUSRPCapture类
"""

import importlib.util
import sys
from pathlib import Path


def load_usrp_module():
    """动态加载123_7.py模块并返回ParallelSyncUSRPCapture类"""
    try:
        # 查找123_7.py文件
        module_path = Path(__file__).parent / "123_7.py"
        
        if not module_path.exists():
            raise FileNotFoundError(f"未找到USRP控制模块: {module_path}")
        
        # 动态导入
        spec = importlib.util.spec_from_file_location("usrp_module", str(module_path))
        usrp_module = importlib.util.module_from_spec(spec)
        sys.modules["usrp_module"] = usrp_module
        spec.loader.exec_module(usrp_module)
        
        # 返回需要的类
        return usrp_module.ParallelSyncUSRPCapture
        
    except Exception as e:
        print(f"❌ 加载USRP模块失败: {e}")
        raise


if __name__ == '__main__':
    # 测试加载
    ParallelSyncUSRPCapture = load_usrp_module()
    print(f"✅ 成功加载: {ParallelSyncUSRPCapture}")
