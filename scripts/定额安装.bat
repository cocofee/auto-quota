@echo off
setlocal enabledelayedexpansion

rem HuggingFace离线模式（用本地缓存，不联网，避免卡住）
set TRANSFORMERS_OFFLINE=1
set HF_HUB_OFFLINE=1
set HF_DATASETS_OFFLINE=1
title ���װ

:MENU
cls
echo.
echo  ========================================
echo    ���װ��ʡ�ݳ�ʼ����
echo  ========================================
echo.
echo    �״�ʹ����ʡ��ʱ����Ҫ���붨�����ݡ�
echo    ��װ��ɺ��ճ�ʹ�ò���Ҫ�����д˹��ߡ�
echo.
echo  ----------------------------------------
echo    [1] ���붨��⣨��ѡ��
echo        ^| �Զ�ɸѡδ�����ʡ��
echo.
echo    [2] ���붨����򣨿�ѡ��
echo        ^| ���붨��˵���ı�������ƥ��׼ȷ��
echo.
echo    [q] �˳�
echo  ----------------------------------------
echo.
set "CHOICE="
set /p "CHOICE=  ��ѡ��: "

if /i "!CHOICE!"=="1" goto IMPORT_QUOTA
if /i "!CHOICE!"=="2" goto IMPORT_RULES
if /i "!CHOICE!"=="q" goto EXIT
if /i "!CHOICE!"=="quit" goto EXIT

echo.
echo  [����] ������ 1-2 �� q
timeout /t 2 >nul
goto MENU

:IMPORT_QUOTA
cd /d "%~dp0.."

echo.
echo ============================================================
echo           ���붨�����ݿ�
echo ============================================================
echo.
echo  ����: ɨ��Excel �Զ�ʶ��רҵ �������ݿ� �ؽ�����
echo.

:: ֻ��ʾδ�����ʡ�ݣ��ѵ�����Զ�����
python tools/_select_province.py --only-new
if errorlevel 1 (
    pause
    goto MENU
)

:: ��ȡPythonд��ʡ����
set /p PROVINCE=<.tmp_selected_province.txt
del /q .tmp_selected_province.txt 2>nul

if not defined PROVINCE (
    echo [����] δѡ��ʡ��
    pause
    goto MENU
)

echo.
echo ============================================================
echo  ʡ��: !PROVINCE!
echo  ����: ���붨�� + �ؽ�����
echo ============================================================
echo.
echo  ע��: ��ͬרҵ�ľ����ݻᱻ�滻����ͬרҵ����Ӱ��
echo.
set /p "CONFIRM=ȷ�Ͽ�ʼ����? [Y/n]: "
if /i "!CONFIRM!"=="n" goto MENU

echo.
echo ============================================================
echo  ��ʼ����...
echo ============================================================
echo.

python tools/import_all.py --province "!PROVINCE!"

echo.
echo ============================================================
echo  �������!
echo ============================================================
echo.

del /q .tmp_selected_province.txt 2>nul
pause
goto MENU

:IMPORT_RULES
cd /d "%~dp0.."

echo.
echo ============================================================
echo   ���붨�����
echo ============================================================
echo.
echo   ʹ��˵����
echo   1. �� knowledge\�����\ �°�ʡ�ݽ��ļ���
echo   2. �Ѷ���˵���ı��ļ�(.txt)�ŵ���Ӧʡ���ļ�����
echo   3. ���д˹����Զ��������������
echo.
echo   �ļ��нṹʾ����
echo     knowledge\�����\����2024\��װ����˵��.txt
echo     knowledge\�����\����2024\����ˮ����˵��.txt
echo     knowledge\�����\ɽ��2024\��װ����˵��.txt
echo.
echo ============================================================
echo.

REM ���Ŀ¼
if not exist "knowledge\�����\" (
    echo [��ʾ] knowledge\�����\ Ŀ¼�����ڣ����ڴ���...
    mkdir "knowledge\�����"
    echo.
    echo �Ѵ���Ŀ¼���밴���²��������
    echo   1. �� knowledge\�����\ ���½�ʡ���ļ���
    echo   2. �Ѷ���˵���ı��Ž�ȥ��.txt��ʽ��
    echo   3. �ٴ����д˹���
    echo.
    pause
    goto MENU
)

python srcule_knowledge.py import

echo.
python srcule_knowledge.py stats

echo.
pause
goto MENU

:EXIT
echo.
echo  �ټ�!
echo.