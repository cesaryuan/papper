# Export only test-owned DOCX copies through Microsoft Word's actual pagination.
# Python writes a UTF-8 JSON request and invokes this file with Windows PowerShell
# (-NoProfile -ExecutionPolicy Bypass -File), never an interpolated COM command.
# A probe records Word's renderer/printer/options without changing application state.
# Rendering opens a unique read-only invisible document, exports PDF document content
# without review markup, and closes only that owned document in finally. User documents
# and global Word options remain intact; Application.Quit is never called.
# An opt-in update_fields request updates directories until pagination stabilizes;
# fixed DATE fields are locked by the Python visual-copy preparation before Open.
# All test-owned copies disable automatic field updates before Word opens them,
# preventing the "fields may refer to other files" confirmation during testing.
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

function Update-VisualFields {
    # Update only this test-owned document. Directory expansion changes pagination,
    # so repeat until page count and directory text (including page numbers) agree.
    param($Document)
    $previous = $null
    for ($attempt = 0; $attempt -lt 6; $attempt++) {
        [void]$Document.Fields.Update()
        foreach ($section in $Document.Sections) {
            foreach ($story in @($section.Headers, $section.Footers)) {
                foreach ($variant in $story) {
                    if ($variant.Exists) { [void]$variant.Range.Fields.Update() }
                }
            }
        }
        $Document.Repaginate()
        $directoryText = @($Document.Fields | Where-Object { $_.Code.Text.Trim().StartsWith('TOC ') } |
            ForEach-Object { [string]$_.Result.Text })
        $state = ([string]$Document.ComputeStatistics(2)) + '|' + ($directoryText -join '|')
        if ($state -eq $previous) { return }
        $previous = $state
    }
    throw 'Word directory pagination did not stabilize after six field updates'
}

function New-WordVisualCopy {
    # Word may prompt on updateFields or dirty fields even for a read-only invisible
    # document. Disable both only in the owned ZIP; explicit updates run after Open.
    param([string]$Source, [string]$Destination)
    Copy-Item -LiteralPath $Source -Destination $Destination
    # Windows PowerShell 5.1 does not load ZipArchiveMode through FileSystem alone.
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::Open($Destination, [IO.Compression.ZipArchiveMode]::Update)
    try {
        foreach ($entry in @($archive.Entries)) {
            if ($entry.FullName -notmatch '^word/.*\.xml$') { continue }
            $stream = $entry.Open()
            try {
                $xml = New-Object Xml.XmlDocument
                $xml.PreserveWhitespace = $true
                $xml.Load($stream)
            } finally { $stream.Dispose() }
            $namespace = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
            $namespaces = New-Object Xml.XmlNamespaceManager($xml.NameTable)
            $namespaces.AddNamespace('w', $namespace)
            $changed = $false
            if ($entry.FullName -eq 'word/settings.xml') {
                $setting = $xml.SelectSingleNode('/w:settings/w:updateFields', $namespaces)
                if ($null -eq $setting) {
                    $setting = $xml.CreateElement('w', 'updateFields', $namespace)
                    [void]$xml.DocumentElement.AppendChild($setting)
                }
                [void]$setting.SetAttribute('val', $namespace, 'false')
                $changed = $true
            }
            foreach ($field in $xml.SelectNodes('//w:fldChar[@w:dirty] | //w:fldSimple[@w:dirty]', $namespaces)) {
                [void]$field.SetAttribute('dirty', $namespace, 'false')
                $changed = $true
            }
            if ($changed) {
                $stream = $entry.Open()
                try {
                    $stream.SetLength(0)
                    $xml.Save($stream)
                } finally { $stream.Dispose() }
            }
        }
    } finally { $archive.Dispose() }
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
        New-WordVisualCopy $source $ownedSource
        $documents = $application.Documents
        # Open(FileName, ConfirmConversions, ReadOnly, AddToRecentFiles, ...,
        # Visible=false); never change global application visibility/options.
        # Supply explicit defaults through Visible: PowerShell's COM binder can
        # reject Type.Missing for Word's optional ref-object arguments.
        $document = $documents.Open($ownedSource, $false, $true, $false,
            '', '', $false, '', '', 0, 50001, $false)
        if ($request.update_fields) { Update-VisualFields $document }
        if ($request.inspect_numbering) {
            # Read Word's rendered list labels separately from TOC field text:
            # inherited TOC numbering can duplicate labels and advance headings.
            $numbering = @($document.Paragraphs | ForEach-Object {
                [ordered]@{
                    text = $_.Range.Text.Trim()
                    list_number = [string]$_.Range.ListFormat.ListString
                }
            })
        }
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
    if ($request.inspect_numbering) { $result.numbering = $numbering }
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
