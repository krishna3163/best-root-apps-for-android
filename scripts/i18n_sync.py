"""Multilingual README Synchronizer for Root Apps (i18n).

Synchronizes README.md into README.zh-CN.md (Simplified Chinese)
and README.ru.md (Russian) with root/module-specific vocabulary.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README_EN = ROOT / "README.md"
README_ZH = ROOT / "README.zh-CN.md"
README_RU = ROOT / "README.ru.md"

ZH_TRANSLATIONS = {
    "Best Root Apps for Android": "最佳安卓 Root 应用与模块精选目录",
    "A curated directory for rooted Android devices": "专为安卓 Root 设备打造的精选神器与模块合集",
    "Explore 500+ apps, Magisk, KernelSU and LSPosed modules, system tools, privacy utilities and practical rooting guides.": "探索 500+ 款精选 Root 应用、Magisk/KernelSU/APatch/LSPosed 模块、系统底层运维与刷机实用教程。",
    "Table of Contents": "目录导航",
    "Introduction": "项目介绍",
    "About": "关于本项目",
    "Rooting Guides": "Root 刷机指南",
    "No-Root Shizuku Alternative": "免 Root Shizuku 替代方案",
    "The 4-Step Rooting Roadmap": "四步 Root 路线图",
    "Device-Specific Guides": "特定机型指南",
    "Additional Resources": "扩展资源",
    "Connect with Maintainer": "联系维护者",
    "Community Chat & Discussions": "社区聊天与互动讨论",
    "Community Chat": "社区交流群",
    "Starter Kit: Must have Apps": "新手必备入门神器",
    "Root Managers": "Root 管理器与授权工具",
    "Module Managers": "模块管理器",
    "Metamodules": "元模块与挂载工具",
    "LSPosed & Xposed": "LSPosed 与 Xposed 框架",
    "Zygisk": "Zygisk 注入模块",
    "Root Hiding & Play Integrity": "Root 隐藏与 Play 完整性伪装",
    "Bootloop Protection": "救砖防卡米保护",
    "Root Detection & Testing": "Root 检测与测试",
    "Audio & Sound Mods": "音频与音质优化",
    "Gaming & Performance": "游戏优化与性能调度",
    "Privacy & Security": "隐私防护与权限控制",
    "System & Utilities": "系统运维与工具箱",
    "Legal and Safety": "免责声明与安全警告",
    "Android Power-User Ecosystem": "安卓极客与高级用户生态圈",
}

RU_TRANSLATIONS = {
    "Best Root Apps for Android": "Лучшие Root приложения и модули для Android",
    "A curated directory for rooted Android devices": "Курируемый каталог для рутированных Android устройств",
    "Explore 500+ apps, Magisk, KernelSU and LSPosed modules, system tools, privacy utilities and practical rooting guides.": "Более 500 приложений, модулей Magisk, KernelSU, APatch и LSPosed, системных твиков и руководств по рутированию.",
    "Table of Contents": "Содержание",
    "Introduction": "Введение",
    "About": "О проекте",
    "Rooting Guides": "Инструкции по рутированию",
    "No-Root Shizuku Alternative": "Альтернатива без Root (Shizuku)",
    "The 4-Step Rooting Roadmap": "4 шага к получению Root",
    "Device-Specific Guides": "Инструкции для устройств",
    "Additional Resources": "Дополнительные ресурсы",
    "Connect with Maintainer": "Связаться с автором",
    "Community Chat & Discussions": "Чат сообщества и обсуждения",
    "Community Chat": "Чат сообщества",
    "Starter Kit: Must have Apps": "Стартовый набор: обязательные приложения",
    "Root Managers": "Менеджеры Root",
    "Module Managers": "Менеджеры модулей",
    "Metamodules": "Метамодули",
    "LSPosed & Xposed": "LSPosed и Xposed",
    "Zygisk": "Модули Zygisk",
    "Root Hiding & Play Integrity": "Скрытие Root и обход Play Integrity",
    "Bootloop Protection": "Защита от бутлупа",
    "Root Detection & Testing": "Тестирование и детекторы Root",
    "Audio & Sound Mods": "Звук и аудиомоды",
    "Gaming & Performance": "Игры и производительность",
    "Privacy & Security": "Приватность и безопасность",
    "System & Utilities": "Системные утилиты",
    "Legal and Safety": "Правовая информация и безопасность",
    "Android Power-User Ecosystem": "Экосистема для энтузиастов Android",
}


def translate_root_readme(text: str, lang: str) -> str:
    mapping = ZH_TRANSLATIONS if lang == "zh-CN" else RU_TRANSLATIONS
    res = text
    for src, dst in mapping.items():
        res = res.replace(src, dst)
    return res


def sync_all() -> tuple[Path, Path]:
    content = README_EN.read_text(encoding="utf-8")
    zh_content = translate_root_readme(content, "zh-CN")
    ru_content = translate_root_readme(content, "ru")

    README_ZH.write_text(zh_content, encoding="utf-8")
    README_RU.write_text(ru_content, encoding="utf-8")
    return README_ZH, README_RU


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize multilingual root apps READMEs.")
    args = parser.parse_args()
    zh, ru = sync_all()
    print(f"✅ Generated {zh.name} and {ru.name}")
    return 0


if __name__ == "__main__":
    main()
