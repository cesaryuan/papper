# Export only test-owned DOCX copies through Microsoft Word's actual pagination.
# Python writes a UTF-8 JSON request and invokes this file with Windows PowerShell
# (-NoProfile -ExecutionPolicy Bypass -File), never an interpolated COM command.
# A probe records Word's renderer/printer/options without changing application state.
# Rendering opens a unique read-only invisible document, exports PDF document content
# without review markup, and closes only that owned document in finally. User documents
# and global Word options remain intact; Application.Quit is never called.
param([string]$RequestPath = "$PSScriptRoot/results/word-request.json")

$ErrorActionPreference = 'Stop'
$request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$application = $null
$document = $null
$documents = $null

function Get-WordApplication {
    # Prefer ROT attachment; an existing but inaccessible Word must not start a
    # second instance. Run this script at the same permission level as Word.
    try {
        return [Runtime.InteropServices.Marshal]::GetActiveObject('Word.Application')
    } catch {
        if (Get-Process -Name WINWORD -ErrorAction SilentlyContinue) {
            throw 'Cannot attach to running Word via ROT; run Windows PowerShell at the same permission level as Word'
        }
        $ownedApplication = New-Object -ComObject Word.Application
        $ownedApplication.Visible = $false
        return $ownedApplication
    }
}

function Get-WordEnvironment {
    # Printer metrics, Word build and print options can alter pagination/pixels.
    param($Word)
    $executable = Join-Path $Word.Path 'WINWORD.EXE'
    $version = [Diagnostics.FileVersionInfo]::GetVersionInfo($executable)
    # uv may inherit PowerShell 7's PSModulePath, hiding Windows PowerShell's
    # Get-FileHash cmdlet. Hash through .NET so the probe stays independent of it.
    $hasher = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($executable)
    try {
        $executableHash = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    } finally {
        $stream.Dispose()
        $hasher.Dispose()
    }
    return [ordered]@{
        word_version = [string]$Word.Version
        word_build = [string]$Word.Build
        word_file_version = $version.FileVersion
        word_sha256 = $executableHash
        active_printer = [string]$Word.ActivePrinter
        ui_language = [int]$Word.LanguageSettings.LanguageID(2)
        print_hidden_text = [bool]$Word.Options.PrintHiddenText
        print_field_codes = [bool]$Word.Options.PrintFieldCodes
        print_drawing_objects = [bool]$Word.Options.PrintDrawingObjects
        print_backgrounds = [bool]$Word.Options.PrintBackgrounds
    }
}

try {
    $application = Get-WordApplication
    $before = @($application.Documents | ForEach-Object { [string]$_.FullName })
    $environment = Get-WordEnvironment $application
    if ($request.mode -eq 'export') {
        $source = [IO.Path]::GetFullPath([string]$request.source)
        $output = [IO.Path]::GetFullPath([string]$request.output)
        # A unique copy prevents Documents.Open from returning a user-owned document.
        [string]$ownedSource = Join-Path (Split-Path $output) ('word-owned-' + [Guid]::NewGuid().ToString('N') + '.docx')
        Copy-Item -LiteralPath $source -Destination $ownedSource
        $documents = $application.Documents
        # Open(FileName, ConfirmConversions, ReadOnly, AddToRecentFiles, ...,
        # Visible=false); never change global application visibility/options.
        # Supply explicit defaults through Visible: PowerShell's COM binder can
        # reject Type.Missing for Word's optional ref-object arguments.
        $document = $documents.Open($ownedSource, $false, $true, $false,
            '', '', $false, '', '', 0, 50001, $false)
        $document.Repaginate()
        # wdExportFormatPDF=17, wdExportOptimizeForPrint=0, wdExportAllDocument=0,
        # wdExportDocumentContent=0 excludes review balloons/markup from pagination.
        $document.ExportAsFixedFormat($output, 17, $false, 0, 0, 1, 1, 0,
            $false, $true, 0, $true, $true, $false)
        if (-not (Test-Path -LiteralPath $output)) {
            throw "Word did not create PDF: $output"
        }
        $document.Close(0)
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
        $document = $null
    } elseif ($request.mode -ne 'probe') {
        throw "Unknown Word request mode: $($request.mode)"
    }
    $after = @($application.Documents | ForEach-Object { [string]$_.FullName })
    # New user documents opened during a run are fine; pre-existing documents
    # must still be open. This also handles Word starting with no open documents.
    if (@($before | Where-Object { $after -notcontains $_ }).Count -ne 0) {
        throw 'A pre-existing Word document is no longer open after visual export'
    }
    $result = [ordered]@{ environment = $environment; user_documents_preserved = $true }
    $result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $request.result -Encoding UTF8
} finally {
    if ($null -ne $document) {
        # Discard only changes to the owned read-only copy, including export failures.
        $document.Close(0)
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
    }
    if ($null -ne $documents) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($documents) }
    if ($null -ne $application) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($application) }
}
