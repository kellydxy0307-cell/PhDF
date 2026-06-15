param(
  [Parameter(Mandatory = $true)]
  [string]$PdfPath,

  [int]$MaxPages = 40,
  [int]$RenderWidth = 1400,
  [int]$MaxChars = 200000,
  [string]$Lang = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

Add-Type -AssemblyName System.Runtime.WindowsRuntime

$methods = [System.WindowsRuntimeSystemExtensions].GetMethods()
$asTaskOperation = ($methods | Where-Object {
  $_.Name -eq "AsTask" -and
  $_.IsGenericMethod -and
  $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
$asTaskAction = ($methods | Where-Object {
  $_.Name -eq "AsTask" -and
  -not $_.IsGenericMethod -and
  $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq "IAsyncAction"
})[0]

function Await-Operation($operation, [Type]$resultType) {
  $asTask = $asTaskOperation.MakeGenericMethod($resultType)
  $task = $asTask.Invoke($null, @($operation))
  $task.Wait() | Out-Null
  return $task.Result
}

function Await-Action($action) {
  $task = $asTaskAction.Invoke($null, @($action))
  $task.Wait() | Out-Null
}

[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Pdf.PdfPageRenderOptions, Windows.Data.Pdf, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime] | Out-Null
[Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime] | Out-Null

if (-not (Test-Path -LiteralPath $PdfPath)) {
  throw "PDF path does not exist: $PdfPath"
}

if ($Lang.Trim()) {
  $language = [Windows.Globalization.Language]::new($Lang.Trim())
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
} else {
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
}

if ($null -eq $engine) {
  throw "Windows OCR engine is not available for the requested language."
}

$file = Await-Operation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($PdfPath)) ([Windows.Storage.StorageFile])
$pdf = Await-Operation ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
$pageCount = [int]$pdf.PageCount
$pagesToRead = [Math]::Min($pageCount, [Math]::Max(1, $MaxPages))
$parts = [System.Collections.Generic.List[string]]::new()
$totalChars = 0

for ($index = 0; $index -lt $pagesToRead; $index++) {
  $page = $null
  try {
    $page = $pdf.GetPage($index)
    $stream = [Windows.Storage.Streams.InMemoryRandomAccessStream]::new()
    $options = [Windows.Data.Pdf.PdfPageRenderOptions]::new()
    $options.DestinationWidth = [uint32]$RenderWidth

    Await-Action ($page.RenderToStreamAsync($stream, $options))
    $stream.Seek(0) | Out-Null

    $decoder = Await-Operation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await-Operation ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

    if ($bitmap.BitmapPixelFormat.ToString() -ne "Bgra8") {
      $bitmap = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert(
        $bitmap,
        [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
        [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied
      )
    }

    $result = Await-Operation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $text = [string]$result.Text

    if ($text.Trim()) {
      $parts.Add("`n`n--- Page $($index + 1) ---`n$text") | Out-Null
      $totalChars += $text.Length
    }

    if ($totalChars -ge $MaxChars) {
      break
    }
  } finally {
    if ($null -ne $page) {
      $page.Dispose()
    }
  }
}

$payload = [PSCustomObject]@{
  ok = $true
  engine = "windows_ocr"
  page_count = $pageCount
  pages_processed = $pagesToRead
  text = ($parts -join "")
}

$payload | ConvertTo-Json -Compress
