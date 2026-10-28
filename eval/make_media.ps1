# 生成多模态测试素材：中文 TTS 音频 + 带中文文字的截图（System.Drawing）
# 用法：pwsh -File make_media.ps1 -OutDir <目录>
param([string]$OutDir = "$PSScriptRoot\fixtures\media")

New-Item -ItemType Directory -Force $OutDir | Out-Null
Add-Type -AssemblyName System.Speech
Add-Type -AssemblyName System.Drawing

function Save-Speech([string]$text, [string]$path) {
    $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $s.SelectVoice("Microsoft Huihui Desktop")
    $s.Rate = 1
    $s.SetOutputToWaveFile($path)
    $s.Speak($text)
    $s.Dispose()
}

function Save-Image([string[]]$lines, [string]$path) {
    $bmp = New-Object System.Drawing.Bitmap 900, (60 + 44 * $lines.Count)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.Clear([System.Drawing.Color]::White)
    $font = New-Object System.Drawing.Font("Microsoft YaHei", 18)
    $y = 30
    foreach ($l in $lines) { $g.DrawString($l, $font, [System.Drawing.Brushes]::Black, 30, $y); $y += 44 }
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
}

# 音频：开网店的受访者，痛点 = 售后消息回复不过来（埋点 P_CS）
Save-Speech ("我是开淘宝店的，主要卖女装。最头疼的是售后，每天晚上要回两三百条消息，" +
    "大部分都是问尺码、问什么时候发货，问题都差不多，可我一个人根本回不过来，" +
    "经常回到半夜一点。之前试过淘宝自带的自动回复，太死板了，客户问得稍微绕一点它就答非所问。" +
    "如果有个工具能帮我把这些重复问题处理掉，一个月三百块我是愿意出的。") "$OutDir\interview_shop.wav"

# 图片：受访者手工对账的 Excel 截图（埋点 P_RECON）
Save-Image @(
    "2026年9月 对账表（手工）",
    "日期      平台     订单金额   到账金额   差额   备注",
    "09-01    淘宝     12,480     12,105    -375   手续费？",
    "09-01    抖音      8,920      8,412    -508   对不上，待查",
    "09-02    拼多多     5,630      5,630       0",
    "……共 312 行，每月底要花两天核对，经常对错"
) "$OutDir\recon_excel.png"

# 视频用的旁白：演示每天手工把订单从三个平台复制到 Excel（埋点 P_RECON）
Save-Speech ("你看，我每天早上要先打开淘宝后台，把昨天的订单导出来，再打开抖音和拼多多，" +
    "一个个复制到这个表里，光这一步就要四十分钟，月底还要再对一遍账。") "$OutDir\video_voice.wav"

Get-ChildItem $OutDir | Select-Object Name, Length
