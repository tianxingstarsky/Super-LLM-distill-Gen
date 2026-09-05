@echo off
rem DataForge 一键启动（单进程融合）：控制台 UI(8501) + 审核中心 API(6900) 一个进程。
rem 无 Redis/ES/Argilla/看门狗——双击即用；关闭控制台进程则全部停止。
cd /d F:\无项目工作文件夹\Super-LLM-distill-Gen
set NO_PROXY=127.0.0.1,localhost
set no_proxy=127.0.0.1,localhost
start "DataForge" .venv\Scripts\pythonw.exe -W ignore -m lib.cli console
echo 控制台: http://localhost:8501  审核中心 API: http://127.0.0.1:6900
