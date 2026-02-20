@echo off
setlocal enabledelayedexpansion
title �Զ��׶���ϵͳ
cd /d "%~dp0"

echo ============================================================
echo           �Զ��׶���ϵͳ - һ������
echo ============================================================
echo.
echo  ����: ƥ�� - ����Excel+����ļ� - Claude Code��� - ��������
echo.

:: ============================================================
:: ѡ��ʡ�ݣ���Python����bat����Ƕ�����⣩
:: ============================================================
python tools/_select_province.py
if errorlevel 1 (
    pause
    exit /b 1
)
set /p PROVINCE=<.tmp_selected_province.txt
del /q .tmp_selected_province.txt 2>nul
echo.
echo.

:: ============================================================
:: �ȴ������ļ�
:: ============================================================
:WAIT_FILE
echo ============================================================
echo  ʡ��: !PROVINCE!  ģʽ: ����+�����
echo ============================================================
echo.
echo  �뽫�嵥Excel�ļ���ק���˴��ڣ�Ȼ�󰴻س�:
echo    q=�˳�
echo.
set "INPUT_FILE="
set /p "INPUT_FILE="
set "INPUT_FILE=!INPUT_FILE:"=!"

if /i "!INPUT_FILE!"=="q" goto EXIT
if /i "!INPUT_FILE!"=="quit" goto EXIT
if /i "!INPUT_FILE!"=="exit" goto EXIT

if not exist "!INPUT_FILE!" (
    echo.
    echo  [����] �ļ�������: !INPUT_FILE!
    echo.
    goto WAIT_FILE
)

set "CURRENT_FILE=!INPUT_FILE!"

:: ============================================================
:: ִ��ƥ��
:: ============================================================
:RUN_MATCH
echo.
echo ============================================================
echo  ��ʼƥ��...
echo  �ļ�: !CURRENT_FILE!
echo  ʡ��: !PROVINCE!
echo ============================================================
echo.

python tools/jarvis_pipeline.py "!CURRENT_FILE!" --province "!PROVINCE!"

set "LAST_OUTPUT="
for /f "delims=" %%F in ('dir /b /od "output\ƥ����_*.xlsx" 2^>nul') do (
    set "LAST_OUTPUT=output\%%F"
)

echo.
if defined LAST_OUTPUT (
    echo  Excel���: !LAST_OUTPUT!
)
echo  ����ļ�: output\review\ Ŀ¼
echo.
echo  ��һ��: ������ļ����� Claude Code ���

:: ============================================================
:: �����˵�
:: ============================================================
:POST_MATCH
echo.
echo ============================================================
echo  ��������ʲô?
echo ============================================================
echo.
echo   [1] �򿪽��Excel - ���������
echo   [2] ������ļ�Ŀ¼ - ����Claude Code���
echo   [3] �������� - �Ա�ѧϰ
echo   [4] ����ƥ�䵱ǰ�ļ�
echo   [5] ƥ�����ļ�
echo   [q] �˳�
echo.
set "ACTION="
set /p "ACTION=��ѡ��: "

if /i "!ACTION!"=="1" goto OPEN_RESULT
if /i "!ACTION!"=="2" goto OPEN_REVIEW
if /i "!ACTION!"=="3" goto IMPORT_CORRECTION
if /i "!ACTION!"=="4" goto RUN_MATCH
if /i "!ACTION!"=="5" goto WAIT_FILE
if /i "!ACTION!"=="q" goto EXIT
if /i "!ACTION!"=="quit" goto EXIT

echo  ��Чѡ�������� 1-5 �� q
goto POST_MATCH

:: ============================================================
:: [1] �򿪽��Excel
:: ============================================================
:OPEN_RESULT
if defined LAST_OUTPUT (
    echo.
    echo  ���ڴ�: !LAST_OUTPUT!
    start "" "!LAST_OUTPUT!"
) else (
    echo.
    echo  δ�ҵ�����ļ�����������ƥ��
)
goto POST_MATCH

:: ============================================================
:: [2] ������ļ�Ŀ¼
:: ============================================================
:OPEN_REVIEW
echo.
echo  ���ڴ�����ļ�Ŀ¼...
echo.
echo  ʹ�÷���:
echo    1. �� output\review\ �е�����ļ����� Claude Code
echo    2. Claude Code ��������ˣ�������˱���Excel
echo    3. ��ȷ�Ϻ�Claude Code ���������뾭���
echo    4. ����ѡ [4] ����ƥ�䣬���Ľ�Ч��
echo.
start "" "output\review"
goto POST_MATCH

:: ============================================================
:: [3] ��������
:: ============================================================
:IMPORT_CORRECTION
echo.
echo ============================================================
echo  �������� - �Ա�ѧϰ
echo ============================================================
echo.

if defined LAST_OUTPUT (
    echo  ��⵽�ϴ����: !LAST_OUTPUT!
    echo  �������Ϊԭʼ�ļ�? [Y/n]
    set /p "USE_LAST=  "
    if /i "!USE_LAST!"=="n" (
        echo.
        echo  ������ԭʼ���Excel:
        set /p "ORIGINAL_FILE="
        set "ORIGINAL_FILE=!ORIGINAL_FILE:"=!"
    ) else (
        set "ORIGINAL_FILE=!LAST_OUTPUT!"
    )
) else (
    echo  ������ԭʼ���Excel:
    set /p "ORIGINAL_FILE="
    set "ORIGINAL_FILE=!ORIGINAL_FILE:"=!"
)

if not exist "!ORIGINAL_FILE!" (
    echo  �ļ�������: !ORIGINAL_FILE!
    goto POST_MATCH
)

echo.
echo  �������������Excel:
set /p "CORRECTED_FILE="
set "CORRECTED_FILE=!CORRECTED_FILE:"=!"

if not exist "!CORRECTED_FILE!" (
    echo  �ļ�������: !CORRECTED_FILE!
    goto POST_MATCH
)

echo.
echo  ��ʼ�Ա�ѧϰ...
python -m src.diff_learner "!ORIGINAL_FILE!" "!CORRECTED_FILE!" --province "!PROVINCE!"

echo.
echo  ѧϰ���! ѡ [4] ����ƥ�俴Ч����
goto POST_MATCH

:: ============================================================
:: �˳�
:: ============================================================
:EXIT
del /q .tmp_selected_province.txt 2>nul
echo.
echo  �ټ�!
echo.
pause
