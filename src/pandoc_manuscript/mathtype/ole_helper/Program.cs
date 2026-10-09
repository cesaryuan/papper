using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using Microsoft.Win32;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text;
using MTSDKDN;

internal static class Program
{
    private const int S_OK = 0;
    private const int E_NOTIMPL = unchecked((int)0x80004001);
    private const int OLECLOSE_SAVEIFDIRTY = 0;
    private const int CF_METAFILEPICT = 3;
    private const int DVASPECT_CONTENT = 1;
    private const int MM_LOMETRIC = 2;
    private const int MM_HIMETRIC = 3;
    private const int MM_LOENGLISH = 4;
    private const int MM_HIENGLISH = 5;
    private const int MM_TWIPS = 6;
    private const int MM_ISOTROPIC = 7;
    private const int MM_ANISOTROPIC = 8;
    private const int TYMED_HGLOBAL = 1;
    private const int TYMED_MFPICT = 32;
    private const int STGM_CREATE = 0x00001000;
    private const int STGM_READWRITE = 0x00000002;
    private const int STGM_SHARE_EXCLUSIVE = 0x00000010;
    private const short MTINIT_LAUNCH_NOW = 1;
    private const short MTPRF_MODE_NEXT_EQN = 1;
    private const int MTDIM_WIDTH = 1;
    private const int MTDIM_HEIGHT = 2;
    private const int MTDIM_BASELINE = 3;
    private const int MTDIM_HORIZ_POS_TYPE = 4;
    private const int MTDIM_HORIZ_POS = 5;
    private const double MATH_TYPE_DIMENSION_UNITS_PER_POINT = 32.0;
    private const double MATHTYPE_WMF_BASELINE_UNITS_PER_POINT = 16.0;
    private const int PLACEABLE_WMF_HEADER_SIZE = 22;
    private const int WMF_HEADER_SIZE = 18;
    private const ushort META_SETWINDOWORG_FUNCTION = 0x020B;
    private const ushort META_SETWINDOWEXT_FUNCTION = 0x020C;
    private const ushort META_SETTEXTALIGN_FUNCTION = 0x012E;
    private const ushort META_ESCAPE_FUNCTION = 0x0626;
    private const ushort MFCOMMENT_ESCAPE = 15;
    private const ushort MATHTYPE_BASELINE_COMMENT_TYPE = 0;
    private const short MT_OK = 0;
    private const string MathTypeProgId = "Equation.DSMT4";

    private static readonly Guid IidIOleObject = new Guid("00000112-0000-0000-C000-000000000046");
    private static readonly string[] MathTypeProcessNames = new[] { "MathType", "MathTypeLib" };
    private static readonly byte[] MathTypeBaselineSignature = Encoding.ASCII.GetBytes("MathType");
    private static bool mathTypeApiDllResolved;
    private static string? mathTypeApiDllPath;

    [STAThread]
    public static int Main(string[] args)
    {
        ConfigureConsoleEncoding();
        CloseStaleBackgroundMathTypeProcesses();
        var existingMathTypeProcessIds = SnapshotMathTypeProcessIds();
        try
        {
            var options = Options.Parse(args);
            OleCheck(OleInitialize(IntPtr.Zero), "OleInitialize");
            try
            {
                CreateOleBins(options, existingMathTypeProcessIds);
            }
            finally
            {
                OleUninitialize();
                CloseMathTypeProcessesOpenedByHelper(existingMathTypeProcessIds);
            }
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("[ole-helper] " + SafeExceptionMessage(ex));
            if (VerboseLoggingEnabled())
            {
                WriteExceptionDetails(ex);
            }
            CloseMathTypeProcessesOpenedByHelper(existingMathTypeProcessIds);
            return 1;
        }
    }

    /// <summary>
    /// Force UTF-8 so localized helper failures survive Python stderr capture.
    /// </summary>
    private static void ConfigureConsoleEncoding()
    {
        try
        {
            Console.OutputEncoding = new UTF8Encoding(false);
        }
        catch (Exception)
        {
            // Encoding is best-effort; Python also has a legacy-codepage fallback.
        }
    }

    /// <summary>
    /// Print exception fields individually so Exception.ToString failures do not hide the real error.
    /// </summary>
    private static void WriteExceptionDetails(Exception ex)
    {
        var current = ex;
        var depth = 0;
        while (current != null && depth < 8)
        {
            Console.Error.WriteLine($"[ole-helper] exception[{depth}].type: {SafeExceptionType(current)}");
            Console.Error.WriteLine($"[ole-helper] exception[{depth}].hresult: 0x{current.HResult:X8}");
            Console.Error.WriteLine($"[ole-helper] exception[{depth}].message: {SafeExceptionMessage(current)}");

            var stackTrace = SafeStackTrace(current);
            if (!string.IsNullOrWhiteSpace(stackTrace))
            {
                Console.Error.WriteLine($"[ole-helper] exception[{depth}].stack:");
                Console.Error.WriteLine(stackTrace);
            }

            current = SafeInnerException(current);
            depth++;
        }
    }

    /// <summary>
    /// Read an exception message defensively for unusual COM/runtime failures.
    /// </summary>
    private static string SafeExceptionMessage(Exception ex)
    {
        try
        {
            var message = ex.Message;
            return string.IsNullOrWhiteSpace(message) ? SafeExceptionType(ex) : message;
        }
        catch (Exception messageError)
        {
            return $"failed to read {SafeExceptionType(ex)}.Message ({SafeExceptionType(messageError)})";
        }
    }

    /// <summary>
    /// Return the exception type name without touching Exception.ToString().
    /// </summary>
    private static string SafeExceptionType(Exception ex)
    {
        try
        {
            var type = ex.GetType();
            return type.FullName ?? type.Name;
        }
        catch (Exception)
        {
            return "<unknown exception type>";
        }
    }

    /// <summary>
    /// Read stack trace text defensively because ToString() also depends on it.
    /// </summary>
    private static string? SafeStackTrace(Exception ex)
    {
        try
        {
            return ex.StackTrace;
        }
        catch (Exception stackError)
        {
            return $"<failed to read stack trace: {SafeExceptionType(stackError)}>";
        }
    }

    /// <summary>
    /// Read inner exceptions defensively to keep verbose logging best-effort.
    /// </summary>
    private static Exception? SafeInnerException(Exception ex)
    {
        try
        {
            return ex.InnerException;
        }
        catch (Exception)
        {
            return null;
        }
    }

    /// <summary>
    /// Capture currently running MathType-related server processes so cleanup can avoid user-opened windows.
    /// </summary>
    private static HashSet<int> SnapshotMathTypeProcessIds()
    {
        var ids = new HashSet<int>();
        foreach (var processName in MathTypeProcessNames)
        {
            try
            {
                foreach (var process in Process.GetProcessesByName(processName))
                {
                    using (process)
                    {
                        ids.Add(process.Id);
                    }
                }
            }
            catch (Exception ex)
            {
                Log($"{processName} process snapshot failed: {ex.Message}");
            }
        }
        return ids;
    }

    /// <summary>
    /// Remove hidden MathType server leftovers from earlier failed helper runs before COM activation.
    /// </summary>
    private static void CloseStaleBackgroundMathTypeProcesses()
    {
        foreach (var processName in MathTypeProcessNames)
        {
            Process[] processes;
            try
            {
                processes = Process.GetProcessesByName(processName);
            }
            catch (Exception ex)
            {
                Log($"{processName} process lookup failed during stale cleanup: {ex.Message}");
                continue;
            }

            foreach (var process in processes)
            {
                using (process)
                {
                    try
                    {
                        if (!string.IsNullOrWhiteSpace(process.MainWindowTitle))
                        {
                            continue;
                        }
                    }
                    catch (Exception ex)
                    {
                        Log($"{processName} main window lookup failed for {process.Id}: {ex.Message}");
                    }

                    TerminateMathTypeProcess(process, "stale background cleanup");
                }
            }
        }
    }

    /// <summary>
    /// Close MathType processes started by this OLE run after COM has been released.
    /// </summary>
    private static void CloseMathTypeProcessesOpenedByHelper(HashSet<int> existingProcessIds)
    {
        foreach (var processName in MathTypeProcessNames)
        {
            Process[] processes;
            try
            {
                processes = Process.GetProcessesByName(processName);
            }
            catch (Exception ex)
            {
                Log($"{processName} process lookup failed during cleanup: {ex.Message}");
                continue;
            }

            foreach (var process in processes)
            {
                using (process)
                {
                    if (existingProcessIds.Contains(process.Id))
                    {
                        continue;
                    }

                    TerminateMathTypeProcess(process, "helper cleanup");
                }
            }
        }
    }

    /// <summary>
    /// Close a MathType-related server process, escalating to Kill for hidden OLE servers.
    /// </summary>
    private static void TerminateMathTypeProcess(Process process, string reason)
    {
        try
        {
            Log($"closing {process.ProcessName} process {process.Id} ({reason})");
            if (process.CloseMainWindow() && process.WaitForExit(5000))
            {
                return;
            }

            Log($"killing {process.ProcessName} process {process.Id} ({reason})");
            process.Kill();
            process.WaitForExit(5000);
        }
        catch (Exception ex)
        {
            Log($"MathType process cleanup failed for {process.Id}: {ex.Message}");
        }
    }

    /// <summary>
    /// Convert either one request or a manifest batch while reusing the same helper process.
    /// </summary>
    private static void CreateOleBins(Options options, HashSet<int> existingMathTypeProcessIds)
    {
        if (options.BatchManifestPath is null)
        {
            CreateOleBin(options);
            return;
        }

        foreach (var job in LoadBatchJobs(options))
        {
            CreateOleBin(job);
            // Batch mode keeps one helper process alive on purpose, but
            // MathType's OLE server windows can accumulate across jobs if we
            // wait until process exit to clean them up.
            CloseMathTypeProcessesOpenedByHelper(existingMathTypeProcessIds);
        }
    }

    /// <summary>
    /// Expand a batch manifest into per-formula options while inheriting shared CLI settings.
    /// </summary>
    private static IReadOnlyList<Options> LoadBatchJobs(Options options)
    {
        if (options.BatchManifestPath is null)
        {
            throw new InvalidOperationException("batch jobs require a manifest path");
        }

        using var stream = File.OpenRead(options.BatchManifestPath);
        var serializer = new DataContractJsonSerializer(typeof(BatchJob[]));
        var jobs = serializer.ReadObject(stream) as BatchJob[];
        if (jobs is null || jobs.Length == 0)
        {
            throw new ArgumentException($"Batch manifest is empty: {options.BatchManifestPath}");
        }

        var expanded = new List<Options>(jobs.Length);
        for (var index = 0; index < jobs.Length; index++)
        {
            var job = jobs[index];
            if (string.IsNullOrWhiteSpace(job.Input))
            {
                throw new ArgumentException($"Batch manifest entry {index} is missing input.");
            }

            if (string.IsNullOrWhiteSpace(job.Output))
            {
                throw new ArgumentException($"Batch manifest entry {index} is missing output.");
            }

            expanded.Add(options.WithJob(
                job.Input!,
                job.Output!,
                job.PreviewOutput,
                job.MetadataOutput));
        }
        return expanded;
    }

    /// <summary>
    /// Convert one formula request into OLE/preview outputs.
    /// </summary>
    private static void CreateOleBin(Options options)
    {
        if (!options.Method.Equals("set-data", StringComparison.OrdinalIgnoreCase)
            && !options.Method.Equals("sdk-xform-ole", StringComparison.OrdinalIgnoreCase))
        {
            throw new NotSupportedException($"Unsupported method: {options.Method}");
        }

        if (options.Method.Equals("sdk-xform-ole", StringComparison.OrdinalIgnoreCase))
        {
            if (options.BinaryInput)
            {
                CreateOleBinFromSdkMtef(options);
                return;
            }

            throw new NotSupportedException("sdk-xform-ole requires --binary with a raw MTEF input.");
        }

        Log("resolve CLSID");
        var clsid = Guid.Empty;
        OleCheck(CLSIDFromProgID(MathTypeProgId, out clsid), $"CLSIDFromProgID({MathTypeProgId})");

        if (options.PrefsFilePath is not null)
        {
            Log($"ApplyMathTypePrefs({options.PrefsFilePath})");
            ApplyMathTypePrefs(options.PrefsFilePath);
        }

        Log("create storage");
        var outputPath = options.GetRequiredOutputPath();
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(outputPath))!);
        if (File.Exists(outputPath))
        {
            File.Delete(outputPath);
        }

        OleCheck(
            StgCreateDocfile(
                outputPath,
                STGM_CREATE | STGM_READWRITE | STGM_SHARE_EXCLUSIVE,
                0,
                out var storage),
            "StgCreateDocfile");

        var iidIOleObject = IidIOleObject;

        var site = new MinimalOleClientSite();
        Log("OleCreate");
        OleCheck(
            OleCreate(
                ref clsid,
                ref iidIOleObject,
                0,
                IntPtr.Zero,
                site,
                storage,
                out var created),
            "OleCreate(Equation.DSMT4)");

        var oleObject = (IOleObject)created;
        try
        {
            Log("SetHostNames");
            oleObject.SetHostNames("Pandoc Manuscript Probe", "MathType equation");

            InvokeMathTypeVerbWithRecovery(oleObject, options.PreVerb, site, $"pre DoVerb({options.PreVerb})");

            Log("SetEquationData");
            SetEquationData(created, options);

            if (options.DoVerb)
            {
                InvokeMathTypeVerbWithRecovery(oleObject, 2, site, "DoVerb(2)");
            }

            Log("OleSave");
            var persist = (IPersistStorage)created;
            OleCheck(WriteClassStg(storage, ref clsid), "WriteClassStg");
            OleCheck(OleSave(persist, storage, true), "OleSave");
            OleCheck(storage.Commit(0), "IStorage.Commit");
            if (options.PreviewOutputPath is not null)
            {
                Log("Write WMF preview");
                var preview = WriteWmfPreview(created, options.PreviewOutputPath);
                if (options.MetadataOutputPath is not null)
                {
                    Log("Write metadata");
                    WriteMetadata(options.MetadataOutputPath, preview);
                }
            }
            oleObject.Close(OLECLOSE_SAVEIFDIRTY);
        }
        finally
        {
            try
            {
                oleObject.Close(OLECLOSE_SAVEIFDIRTY);
            }
            catch
            {
                // Best-effort cleanup for failed probes; MathType can keep an
                // OLE server process alive after conversion errors.
            }
            Marshal.ReleaseComObject(created);
            Marshal.ReleaseComObject(storage);
        }

        Log($"wrote {outputPath}, bytes={new FileInfo(outputPath).Length}");
    }

    /// <summary>
    /// Retry one MathType OLE verb after clearing stale hidden servers that can poison COM startup.
    /// </summary>
    private static void InvokeMathTypeVerbWithRecovery(IOleObject oleObject, int verb, IOleClientSite site, string label)
    {
        var rect = new RECT { left = 0, top = 0, right = 1600, bottom = 600 };
        try
        {
            Log(label);
            oleObject.DoVerb(verb, IntPtr.Zero, site, 0, IntPtr.Zero, ref rect);
        }
        catch (COMException ex) when ((uint)ex.HResult == 0x80080005)
        {
            Log($"{label} failed with 0x80080005; retry after stale-server cleanup");
            CloseStaleBackgroundMathTypeProcesses();
            oleObject.DoVerb(verb, IntPtr.Zero, site, 0, IntPtr.Zero, ref rect);
        }
    }

    /// <summary>
    /// Wrap a raw MTEF stream as MathType OLE and ask the SDK to render its WMF preview.
    /// </summary>
    private static void CreateOleBinFromSdkMtef(Options options)
    {
        var inputPath = options.GetRequiredInputPath();
        var outputPath = options.GetRequiredOutputPath();
        var mtef = File.ReadAllBytes(inputPath);
        if (mtef.Length == 0)
        {
            throw new InvalidOperationException($"MTEF input is empty: {inputPath}");
        }

        var oleBytes = Convert.FromBase64String(MathTypeSDK.getOLEBase64(mtef));
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(outputPath))!);
        File.WriteAllBytes(outputPath, oleBytes);
        Log($"SDK MTEF wrote {outputPath}, mtef={mtef.Length} bytes, ole={oleBytes.Length} bytes");

        if (options.PreviewOutputPath is null)
        {
            return;
        }

        TryConfigureMathTypeApiDll(required: true);
        MtCheck(MTAPIConnect(0, 30), "MTAPIConnect(mtef-preview)");
        try
        {
            var transformPrefs = options.PrefsFilePath is null
                ? null
                : ReadMathTypePrefsFromFile(options.PrefsFilePath);
            var preview = WriteSdkTransformWmf(MathTypeSDK.Instance, mtef, options.PreviewOutputPath, transformPrefs);
            if (options.MetadataOutputPath is not null)
            {
                WriteMetadata(options.MetadataOutputPath, preview);
            }
        }
        finally
        {
            MTAPIDisconnect();
        }
    }

    /// <summary>
    /// Render SDK-generated MTEF to a placeable WMF preview.
    /// </summary>
    private static PreviewMetadata WriteSdkTransformWmf(MathTypeSDK sdk, byte[] mtef, string outputPath, string? transformPrefs)
    {
        var filePreview = TryWriteSdkTransformWmfFile(sdk, mtef, outputPath, transformPrefs);
        if (filePreview is not null)
        {
            return filePreview;
        }

        var pictSize = 1024 * 1024;
        var pictPointer = Marshal.AllocHGlobal(pictSize);
        try
        {
            for (var offset = 0; offset < pictSize; offset++)
            {
                Marshal.WriteByte(pictPointer, offset, 0);
            }

            var bounds = new MTSDKDN.RECT(0, 0, 0, 0);
            var dims = new MTAPI_DIMS(0, ref bounds);
            MtCheck(sdk.MTXFormResetMgn(), "MTXFormReset(pict)");
            ApplySdkTransformPrefs(sdk, transformPrefs);
            var status = sdk.MTXFormEqnMgn(
                MTXFormEqn.mtxfmLOCAL,
                MTXFormEqn.mtxfmMTEF,
                mtef,
                mtef.Length,
                MTXFormEqn.mtxfmLOCAL,
                MTXFormEqn.mtxfmPICT,
                pictPointer,
                pictSize,
                "",
                ref dims);
            if (status != MathTypeReturnValue.mtOK)
            {
                throw new InvalidOperationException(
                    "MTXFormEqn(MTEF->PICT) returned "
                    + $"{status}; actual={sdk.MTXFormGetStatusMgn(MTXFormStatus.mtxfmSTAT_ACTUAL_LEN)}, "
                    + $"translator={sdk.MTXFormGetStatusMgn(MTXFormStatus.mtxfmSTAT_TRANSL)}, "
                    + $"prefs={sdk.MTXFormGetStatusMgn(MTXFormStatus.mtxfmSTAT_PREF)}");
            }

            var pict = Marshal.PtrToStructure<SdkPictResult>(pictPointer);
            Log(
                $"SDK PICT result mapMode={pict.MapMode}, xExt={pict.XExt}, yExt={pict.YExt}, "
                + $"hMetaFile=0x{pict.HMetaFile.ToInt64():X}, actual={sdk.MTXFormGetStatusMgn(MTXFormStatus.mtxfmSTAT_ACTUAL_LEN)}, "
                + $"head={ReadHexPrefix(pictPointer, 32)}");
            if (pict.HMetaFile == IntPtr.Zero)
            {
                throw new InvalidOperationException("MTXFormEqn(MTEF->PICT) returned an empty metafile handle");
            }

            try
            {
                var bytes = ReadMetaFileBits(pict.HMetaFile);
                if (!HasPlaceableHeader(bytes))
                {
                    bytes = AddPlaceableHeader(bytes, pict.MapMode, pict.XExt, pict.YExt);
                }
                bytes = EnsureWmfWindowRecords(bytes, pict.XExt, pict.YExt);

                Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(outputPath))!);
                File.WriteAllBytes(outputPath, bytes);
                Log($"SDK xform wrote preview {outputPath}, bytes={bytes.Length}");

                var unitsPerInch = UnitsPerInchForMapMode(pict.MapMode);
                var widthPt = Math.Abs(pict.XExt) * 72.0 / unitsPerInch;
                var heightPt = Math.Abs(pict.YExt) * 72.0 / unitsPerInch;
                return new PreviewMetadata(
                    pict.MapMode,
                    pict.XExt,
                    pict.YExt,
                    unitsPerInch,
                    widthPt,
                    heightPt,
                    TryReadMathTypeBaselineFromWmf(bytes, widthPt, heightPt));
            }
            finally
            {
                DeleteMetaFile(pict.HMetaFile);
            }
        }
        finally
        {
            Marshal.FreeHGlobal(pictPointer);
        }
    }

    /// <summary>
    /// Ask MTXFormEqn to write a PICT/WMF preview file directly when memory output is unsupported.
    /// </summary>
    private static PreviewMetadata? TryWriteSdkTransformWmfFile(MathTypeSDK sdk, byte[] mtef, string outputPath, string? transformPrefs)
    {
        try
        {
            var fullOutputPath = Path.GetFullPath(outputPath);
            Directory.CreateDirectory(Path.GetDirectoryName(fullOutputPath)!);
            MtCheck(sdk.MTXFormResetMgn(), "MTXFormReset(pict-file)");
            ApplySdkTransformPrefs(sdk, transformPrefs);
            var bounds = new MTSDKDN.RECT(0, 0, 0, 0);
            var dims = new MTAPI_DIMS(0, ref bounds);
            var status = sdk.MTXFormEqnMgn(
                MTXFormEqn.mtxfmLOCAL,
                MTXFormEqn.mtxfmMTEF,
                mtef,
                mtef.Length,
                MTXFormEqn.mtxfmFILE,
                MTXFormEqn.mtxfmPICT,
                IntPtr.Zero,
                0,
                fullOutputPath,
                ref dims);
            if (status != MathTypeReturnValue.mtOK)
            {
                Log($"MTXFormEqn(MTEF->PICT file) returned {status}");
                return null;
            }
            if (!File.Exists(fullOutputPath))
            {
                Log("MTXFormEqn(MTEF->PICT file) succeeded but wrote no file");
                return null;
            }

            var bytes = File.ReadAllBytes(fullOutputPath);
            if (!HasPlaceableHeader(bytes))
            {
                Log("MTXFormEqn(MTEF->PICT file) wrote a non-placeable file; leaving bytes unchanged");
            }
            bytes = EnsureWmfWindowRecords(bytes, 0, 0);
            File.WriteAllBytes(fullOutputPath, bytes);

            Log($"SDK xform wrote preview file {fullOutputPath}, bytes={bytes.Length}");
            return PreviewMetadataFromSdkOutput(dims, bytes);
        }
        catch (Exception ex)
        {
            Log($"MTXFormEqn(MTEF->PICT file) failed: {SafeExceptionMessage(ex)}");
            return null;
        }
    }

    /// <summary>
    /// Build preview metadata from MTXFormEqn bounds when SDK file output succeeds.
    /// </summary>
    private static PreviewMetadata PreviewMetadataFromSdkOutput(MTAPI_DIMS dims, byte[] bytes)
    {
        if (HasPlaceableHeader(bytes))
        {
            var left = BitConverter.ToInt16(bytes, 6);
            var top = BitConverter.ToInt16(bytes, 8);
            var right = BitConverter.ToInt16(bytes, 10);
            var bottom = BitConverter.ToInt16(bytes, 12);
            var unitsPerInch = BitConverter.ToUInt16(bytes, 14);
            var xExt = right - left;
            var yExt = bottom - top;
            var widthPt = Math.Abs(xExt) * 72.0 / unitsPerInch;
            var heightPt = Math.Abs(yExt) * 72.0 / unitsPerInch;
            return new PreviewMetadata(
                MM_ANISOTROPIC,
                xExt,
                yExt,
                unitsPerInch,
                widthPt,
                heightPt,
                TryReadMathTypeBaselineFromWmf(bytes, widthPt, heightPt));
        }

        try
        {
            var xExt = checked((int)(dims._bounds._right - dims._bounds._left));
            var yExt = checked((int)(dims._bounds._bottom - dims._bounds._top));
            var unitsPerInch = UnitsPerInchForMapMode(MM_HIMETRIC);
            var widthPt = Math.Abs(xExt) * 72.0 / unitsPerInch;
            var heightPt = Math.Abs(yExt) * 72.0 / unitsPerInch;
            return new PreviewMetadata(
                MM_HIMETRIC,
                xExt,
                yExt,
                unitsPerInch,
                widthPt,
                heightPt,
                TryReadMathTypeBaselineFromWmf(bytes, widthPt, heightPt));
        }
        catch (OverflowException)
        {
            Log("MTXFormEqn wrote a non-placeable file with invalid bounds; using byte-size fallback metadata");
            return new PreviewMetadata(MM_HIMETRIC, bytes.Length, bytes.Length, 2540, 72.0, 72.0, null);
        }
    }

    /// <summary>
    /// Read a small unmanaged-memory prefix for SDK buffer diagnostics.
    /// </summary>
    private static string ReadHexPrefix(IntPtr pointer, int length)
    {
        var bytes = new byte[length];
        Marshal.Copy(pointer, bytes, 0, length);
        return BitConverter.ToString(bytes);
    }

    private static void ApplyMathTypePrefs(string prefsFilePath)
    {
        // Applying per-equation size prefs requires MT6.dll. Resolve it lazily
        // so non-default MathType installs do not depend on one hard-coded path.
        TryConfigureMathTypeApiDll(required: true);

        var fullPrefsPath = Path.GetFullPath(prefsFilePath);
        if (!File.Exists(fullPrefsPath))
        {
            throw new FileNotFoundException($"MathType preferences file was not found: {fullPrefsPath}", fullPrefsPath);
        }

        MtCheck(MTAPIConnect(MTINIT_LAUNCH_NOW, 30), "MTAPIConnect(prefs)");
        try
        {
            MtCheck(MTSetMTPrefs(MTPRF_MODE_NEXT_EQN, ReadMathTypePrefsFromFile(fullPrefsPath), -1), "MTSetMTPrefs");
        }
        finally
        {
            MTAPIDisconnect();
        }
    }

    /// <summary>
    /// Load MathType preference bytes as the SDK's internal preference string.
    /// </summary>
    private static string ReadMathTypePrefsFromFile(string prefsFilePath)
    {
        var fullPrefsPath = Path.GetFullPath(prefsFilePath);
        if (!File.Exists(fullPrefsPath))
        {
            throw new FileNotFoundException($"MathType preferences file was not found: {fullPrefsPath}", fullPrefsPath);
        }

        var prefLength = MTGetPrefsFromFile(fullPrefsPath, null, 0);
        if (prefLength <= 0)
        {
            throw new InvalidOperationException($"MTGetPrefsFromFile returned invalid length {prefLength} for {fullPrefsPath}");
        }

        var prefs = new StringBuilder(prefLength);
        MtCheck(MTGetPrefsFromFile(fullPrefsPath, prefs, checked((short)prefLength)), "MTGetPrefsFromFile");
        return prefs.ToString();
    }

    /// <summary>
    /// Apply EQP prefs to the next SDK transform; MTXFormReset clears prior xform state.
    /// </summary>
    private static void ApplySdkTransformPrefs(MathTypeSDK sdk, string? transformPrefs)
    {
        if (transformPrefs is null)
        {
            return;
        }

        Log("MTXFormSetPrefs(user)");
        MtCheck(
            sdk.MTXFormSetPrefsMgn(MTXTranslatorPreference.mtxfmPREF_USER, transformPrefs),
            "MTXFormSetPrefs");
    }

    private static void SetEquationData(object created, Options options)
    {
        Log("cast IDataObject");
        var dataObject = (System.Runtime.InteropServices.ComTypes.IDataObject)created;
        var payload = BuildPayload(options);

        var format = new FORMATETC
        {
            cfFormat = unchecked((short)payload.FormatId),
            dwAspect = (DVASPECT)DVASPECT_CONTENT,
            lindex = -1,
            ptd = IntPtr.Zero,
            tymed = (TYMED)TYMED_HGLOBAL,
        };
        var medium = new STGMEDIUM
        {
            tymed = (TYMED)TYMED_HGLOBAL,
            unionmember = CopyToHGlobal(payload.Bytes),
            pUnkForRelease = null,
        };

        try
        {
            Log($"IDataObject.SetData({options.Format}, {payload.Bytes.Length} bytes)");
            // Keep HGLOBAL ownership in this helper. Some legacy OLE servers are
            // sensitive to fRelease ownership for custom clipboard formats.
            dataObject.SetData(ref format, ref medium, false);
        }
        finally
        {
            if (medium.unionmember != IntPtr.Zero)
            {
                GlobalFree(medium.unionmember);
            }
        }

        Log($"SetData({options.Format}) length={payload.Bytes.Length}");
    }

    private static Payload BuildPayload(Options options)
    {
        var formatId = RegisterClipboardFormat(options.Format);
        if (formatId == 0)
        {
            throw new InvalidOperationException($"RegisterClipboardFormat failed for {options.Format}");
        }

        var inputPath = options.GetRequiredInputPath();
        var bytes = options.BinaryInput
            ? File.ReadAllBytes(inputPath)
            : EncodeText(ReadTextInput(options), options.EncodingName);
        return new Payload(formatId, bytes);
    }

    /// <summary>
    /// Read text input from a file, or accept literal TeX passed through --input.
    /// </summary>
    private static string ReadTextInput(Options options)
    {
        var inputPath = options.GetRequiredInputPath();
        if (File.Exists(inputPath))
        {
            return File.ReadAllText(inputPath, Encoding.UTF8);
        }

        if (IsTeXInputFormat(options.Format))
        {
            Log("using literal TeX input");
            return inputPath;
        }

        throw new FileNotFoundException($"Input file was not found: {inputPath}", inputPath);
    }

    /// <summary>
    /// Return whether the clipboard format accepts literal LaTeX formula text.
    /// </summary>
    private static bool IsTeXInputFormat(string formatName)
    {
        return formatName.Equals("TeX Input Language", StringComparison.OrdinalIgnoreCase);
    }

    private static PreviewMetadata WriteWmfPreview(object created, string outputPath)
    {
        var dataObject = (System.Runtime.InteropServices.ComTypes.IDataObject)created;
        var format = new FORMATETC
        {
            cfFormat = CF_METAFILEPICT,
            dwAspect = (DVASPECT)DVASPECT_CONTENT,
            lindex = -1,
            ptd = IntPtr.Zero,
            tymed = (TYMED)TYMED_MFPICT,
        };
        dataObject.GetData(ref format, out var medium);
        try
        {
            if (medium.unionmember == IntPtr.Zero)
            {
                throw new InvalidOperationException("CF_METAFILEPICT returned an empty handle");
            }

            var pictPtr = GlobalLock(medium.unionmember);
            if (pictPtr == IntPtr.Zero)
            {
                throw new InvalidOperationException("GlobalLock failed for METAFILEPICT");
            }

            int mapMode;
            int xExt;
            int yExt;
            IntPtr hMetaFile;
            try
            {
                mapMode = Marshal.ReadInt32(pictPtr, 0);
                xExt = Marshal.ReadInt32(pictPtr, 4);
                yExt = Marshal.ReadInt32(pictPtr, 8);
                hMetaFile = Marshal.ReadIntPtr(pictPtr, IntPtr.Size == 8 ? 16 : 12);
            }
            finally
            {
                GlobalUnlock(medium.unionmember);
            }

            var bytes = ReadMetaFileBits(hMetaFile);
            var unitsPerInch = UnitsPerInchForMapMode(mapMode);
            if (!HasPlaceableHeader(bytes))
            {
                bytes = AddPlaceableHeader(bytes, mapMode, xExt, yExt);
            }

            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(outputPath))!);
            File.WriteAllBytes(outputPath, bytes);
            Log($"wrote preview {outputPath}, bytes={bytes.Length}");
            return new PreviewMetadata(
                mapMode,
                xExt,
                yExt,
                unitsPerInch,
                Math.Abs(xExt) * 72.0 / unitsPerInch,
                Math.Abs(yExt) * 72.0 / unitsPerInch,
                TryReadMathTypeLastDimensions());
        }
        finally
        {
            ReleaseStgMedium(ref medium);
        }
    }

    private static void WriteMetadata(string outputPath, PreviewMetadata preview)
    {
        // The metadata is intentionally tiny JSON so Python can drive Word XML
        // placement without depending on MathType COM at DOCX injection time.
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(outputPath))!);
        File.WriteAllText(outputPath, preview.ToJson(), new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
        Log($"wrote metadata {outputPath}");
    }

    private static MathTypeLastDimensions? TryReadMathTypeLastDimensions()
    {
        try
        {
            // MTGetLastDimension reports 1/32-point units for the most recent
            // MathType-rendered equation. If MathType has no fresh value, keep
            // this optional so the DOCX layer can fall back to preview metrics.
            if (!TryConfigureMathTypeApiDll(required: false))
            {
                return null;
            }
            MtCheck(MTAPIConnect(MTINIT_LAUNCH_NOW, 30), "MTAPIConnect(dimensions)");
            try
            {
                return new MathTypeLastDimensions(
                    MTGetLastDimension(MTDIM_WIDTH),
                    MTGetLastDimension(MTDIM_HEIGHT),
                    MTGetLastDimension(MTDIM_BASELINE),
                    MTGetLastDimension(MTDIM_HORIZ_POS_TYPE),
                    MTGetLastDimension(MTDIM_HORIZ_POS));
            }
            finally
            {
                MTAPIDisconnect();
            }
        }
        catch (Exception ex)
        {
            Log($"dimension read failed: {ex.Message}");
            return null;
        }
    }

    /// <summary>
    /// Read MathType's WMF baseline comment and convert it to existing 1/32-point metadata units.
    /// </summary>
    private static MathTypeLastDimensions? TryReadMathTypeBaselineFromWmf(byte[] bytes, double widthPt, double heightPt)
    {
        var baselineDelta = TryReadMathTypeBaselineDeltaFromWmf(bytes);
        if (baselineDelta is null)
        {
            return null;
        }

        var baselinePt = baselineDelta.Value / MATHTYPE_WMF_BASELINE_UNITS_PER_POINT;
        var dimensions = new MathTypeLastDimensions(
            PointsToMathTypeRaw(widthPt),
            PointsToMathTypeRaw(heightPt),
            PointsToMathTypeRaw(baselinePt),
            0,
            0);
        Log($"WMF MathType baseline delta={baselineDelta.Value}, baseline={baselinePt:F4}pt");
        return dimensions;
    }

    /// <summary>
    /// Scan WMF META_ESCAPE/MFCOMMENT records for MathType's 12-byte baseline comment.
    /// </summary>
    private static ushort? TryReadMathTypeBaselineDeltaFromWmf(byte[] bytes)
    {
        try
        {
            var offset = HasPlaceableHeader(bytes) ? PLACEABLE_WMF_HEADER_SIZE : 0;
            if (bytes.Length < offset + WMF_HEADER_SIZE)
            {
                return null;
            }

            offset += WMF_HEADER_SIZE;
            while (offset + 6 <= bytes.Length)
            {
                var sizeWords = BitConverter.ToUInt32(bytes, offset);
                var function = BitConverter.ToUInt16(bytes, offset + 4);
                if (sizeWords < 3)
                {
                    return null;
                }

                var recordSizeBytes = checked((int)sizeWords * 2);
                var nextOffset = checked(offset + recordSizeBytes);
                if (nextOffset > bytes.Length)
                {
                    return null;
                }

                if (function == META_ESCAPE_FUNCTION)
                {
                    var baselineDelta = TryReadMathTypeBaselineDeltaFromEscape(bytes, offset + 6, nextOffset - offset - 6);
                    if (baselineDelta is not null)
                    {
                        return baselineDelta;
                    }
                }

                if (function == 0)
                {
                    return null;
                }

                offset = nextOffset;
            }
        }
        catch (Exception ex)
        {
            Log($"WMF baseline scan failed: {SafeExceptionMessage(ex)}");
        }

        return null;
    }

    /// <summary>
    /// Parse one META_ESCAPE payload and return MathType's baseline delta when present.
    /// </summary>
    private static ushort? TryReadMathTypeBaselineDeltaFromEscape(byte[] bytes, int offset, int length)
    {
        if (length < 4 || BitConverter.ToUInt16(bytes, offset) != MFCOMMENT_ESCAPE)
        {
            return null;
        }

        var byteCount = BitConverter.ToUInt16(bytes, offset + 2);
        var commentOffset = offset + 4;
        var commentLength = Math.Min(byteCount, length - 4);
        for (var index = 0; index <= commentLength - 12; index++)
        {
            var candidateOffset = commentOffset + index;
            if (!MatchesBytes(bytes, candidateOffset, MathTypeBaselineSignature))
            {
                continue;
            }

            var commentType = BitConverter.ToUInt16(bytes, candidateOffset + 8);
            if (commentType == MATHTYPE_BASELINE_COMMENT_TYPE)
            {
                return BitConverter.ToUInt16(bytes, candidateOffset + 10);
            }
        }

        return null;
    }

    /// <summary>
    /// Convert point units to MathType's 1/32-point dimension metadata.
    /// </summary>
    private static int PointsToMathTypeRaw(double points) =>
        checked((int)Math.Round(Math.Max(0.0, points) * MATH_TYPE_DIMENSION_UNITS_PER_POINT, MidpointRounding.AwayFromZero));

    /// <summary>
    /// Return whether a byte slice starts with the expected ASCII signature.
    /// </summary>
    private static bool MatchesBytes(byte[] bytes, int offset, byte[] expected)
    {
        if (offset < 0 || offset + expected.Length > bytes.Length)
        {
            return false;
        }

        for (var index = 0; index < expected.Length; index++)
        {
            if (bytes[offset + index] != expected[index])
            {
                return false;
            }
        }

        return true;
    }

    private static bool TryConfigureMathTypeApiDll(bool required)
    {
        // MT6.dll is an auxiliary MathType API DLL, not the OLE server itself.
        // Detect it from the registered OLE server first to support custom installs.
        if (mathTypeApiDllResolved)
        {
            if (mathTypeApiDllPath is not null)
            {
                return true;
            }

            if (required)
            {
                throw new FileNotFoundException("Could not find MathType MT6.dll from the registered OLE server or common install folders.");
            }
            return false;
        }

        mathTypeApiDllResolved = true;
        mathTypeApiDllPath = ResolveMathTypeApiDllPath();
        if (mathTypeApiDllPath is null)
        {
            Log("MT6.dll not found; MathType dimension metadata will use preview metrics only");
            if (required)
            {
                throw new FileNotFoundException("Could not find MathType MT6.dll from the registered OLE server or common install folders.");
            }
            return false;
        }

        var directory = Path.GetDirectoryName(mathTypeApiDllPath)!;
        if (!SetDllDirectory(directory))
        {
            throw new InvalidOperationException(
                $"SetDllDirectory failed for {directory}: 0x{Marshal.GetLastWin32Error():X8}");
        }
        Log($"MT6.dll resolved: {mathTypeApiDllPath}");
        return true;
    }

    private static string? ResolveMathTypeApiDllPath()
    {
        // Return the first MT6.dll path found from MathType registry and common roots.
        foreach (var root in CandidateMathTypeRoots().Where(root => !string.IsNullOrWhiteSpace(root)).Distinct(StringComparer.OrdinalIgnoreCase))
        {
            foreach (var relativePath in new[] { Path.Combine("System", "64", "MT6.dll"), Path.Combine("System", "32", "MT6.dll"), "MT6.dll" })
            {
                var candidate = Path.Combine(root, relativePath);
                if (File.Exists(candidate))
                {
                    return candidate;
                }
            }
        }
        return null;
    }

    private static IEnumerable<string> CandidateMathTypeRoots()
    {
        // Yield likely MathType install roots, with registry-derived paths first.
        var oleServerPath = ResolveMathTypeOleServerPath();
        if (oleServerPath is not null)
        {
            var serverDirectory = Path.GetDirectoryName(oleServerPath);
            if (!string.IsNullOrEmpty(serverDirectory))
            {
                yield return serverDirectory;
                var directoryName = Path.GetFileName(serverDirectory);
                var parent = Directory.GetParent(serverDirectory);
                if (parent is not null && string.Equals(directoryName, "System", StringComparison.OrdinalIgnoreCase))
                {
                    yield return parent.FullName;
                }
                if (parent is not null && (string.Equals(directoryName, "64", StringComparison.OrdinalIgnoreCase) || string.Equals(directoryName, "32", StringComparison.OrdinalIgnoreCase)))
                {
                    var grandparent = parent.Parent;
                    if (grandparent is not null && string.Equals(parent.Name, "System", StringComparison.OrdinalIgnoreCase))
                    {
                        yield return grandparent.FullName;
                    }
                }
            }
        }

        foreach (var folder in new[]
        {
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            Environment.GetEnvironmentVariable("ProgramW6432"),
        })
        {
            if (!string.IsNullOrWhiteSpace(folder))
            {
                yield return Path.Combine(folder, "MathType");
            }
        }
    }

    private static string? ResolveMathTypeOleServerPath()
    {
        // Return the registered MathType OLE server executable path when available.
        var clsid = ReadClassesRootDefault($@"{MathTypeProgId}\CLSID");
        if (string.IsNullOrWhiteSpace(clsid))
        {
            return null;
        }

        var command = ReadClassesRootDefault($@"CLSID\{clsid}\LocalServer32")
            ?? ReadClassesRootDefault($@"CLSID\{clsid}\LocalServer");
        return command is null ? null : ParseRegistryExecutablePath(command);
    }

    private static string? ReadClassesRootDefault(string subkey)
    {
        // Read a default HKCR value, trying both registry views for custom installs.
        foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
        {
            try
            {
                using var root = RegistryKey.OpenBaseKey(RegistryHive.ClassesRoot, view);
                using var key = root.OpenSubKey(subkey);
                if (key?.GetValue(null) is string value && !string.IsNullOrWhiteSpace(value))
                {
                    return value.Trim();
                }
            }
            catch (IOException)
            {
            }
            catch (UnauthorizedAccessException)
            {
            }
        }
        return null;
    }

    private static string? ParseRegistryExecutablePath(string command)
    {
        // Extract the executable path from a quoted or unquoted registry command.
        var text = command.Trim();
        if (text.StartsWith("\"", StringComparison.Ordinal))
        {
            var endQuote = text.IndexOf('"', 1);
            if (endQuote > 1)
            {
                return text.Substring(1, endQuote - 1);
            }
        }

        var exeIndex = text.IndexOf(".exe", StringComparison.OrdinalIgnoreCase);
        return exeIndex < 0 ? null : text.Substring(0, exeIndex + 4).Trim();
    }

    private static byte[] ReadMetaFileBits(IntPtr hMetaFile)
    {
        var size = GetMetaFileBitsEx(hMetaFile, 0, IntPtr.Zero);
        if (size == 0)
        {
            throw new InvalidOperationException("GetMetaFileBitsEx returned zero bytes");
        }
        var buffer = Marshal.AllocHGlobal((int)size);
        try
        {
            var written = GetMetaFileBitsEx(hMetaFile, size, buffer);
            if (written != size)
            {
                throw new InvalidOperationException($"GetMetaFileBitsEx wrote {written} of {size} bytes");
            }
            var bytes = new byte[size];
            Marshal.Copy(buffer, bytes, 0, (int)size);
            return bytes;
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
    }

    private static bool HasPlaceableHeader(byte[] bytes)
    {
        return bytes.Length >= 4 && bytes[0] == 0xD7 && bytes[1] == 0xCD && bytes[2] == 0xC6 && bytes[3] == 0x9A;
    }

    private static ushort UnitsPerInchForMapMode(int mapMode)
    {
        // METAFILEPICT extents carry their own map-mode units. Treat isotropic
        // and anisotropic OLE previews as HIMETRIC; using twips here made
        // MathType previews about 1.76x too large in Word.
        return mapMode switch
        {
            MM_LOMETRIC => 254,
            MM_HIMETRIC => 2540,
            MM_LOENGLISH => 100,
            MM_HIENGLISH => 1000,
            MM_TWIPS => 1440,
            MM_ISOTROPIC => 2540,
            MM_ANISOTROPIC => 2540,
            _ => 2540,
        };
    }

    private static byte[] AddPlaceableHeader(byte[] wmfBytes, int mapMode, int xExt, int yExt)
    {
        var right = ClampPositiveInt16(xExt);
        var bottom = ClampPositiveInt16(yExt);
        var unitsPerInch = UnitsPerInchForMapMode(mapMode);
        Log($"METAFILEPICT mapMode={mapMode}, xExt={xExt}, yExt={yExt}, unitsPerInch={unitsPerInch}");
        var header = new byte[22];
        using var stream = new MemoryStream(header);
        using var writer = new BinaryWriter(stream);
        writer.Write(0x9AC6CDD7u);
        writer.Write((ushort)0);
        writer.Write((short)0);
        writer.Write((short)0);
        writer.Write(right);
        writer.Write(bottom);
        writer.Write(unitsPerInch);
        writer.Write(0u);
        writer.Write((ushort)0);
        var checksum = ComputePlaceableChecksum(header);
        BitConverter.GetBytes(checksum).CopyTo(header, 20);
        return header.Concat(wmfBytes).ToArray();
    }

    /// <summary>
    /// Add WMF window records missing from MathType SDK previews.
    /// </summary>
    private static byte[] EnsureWmfWindowRecords(byte[] bytes, int fallbackXExt, int fallbackYExt)
    {
        if (!TryGetWmfHeaderOffset(bytes, out var wmfOffset))
        {
            return bytes;
        }
        if (WmfHasRecord(bytes, wmfOffset, META_SETWINDOWEXT_FUNCTION))
        {
            return bytes;
        }

        var (windowX, windowY) = WmfWindowExtents(bytes, wmfOffset, fallbackXExt, fallbackYExt);
        var insertOffset = FindWindowRecordInsertOffset(bytes, wmfOffset);
        var records = BuildSetWindowRecords(windowX, windowY);
        var patched = new byte[bytes.Length + records.Length];
        Buffer.BlockCopy(bytes, 0, patched, 0, insertOffset);
        Buffer.BlockCopy(records, 0, patched, insertOffset, records.Length);
        Buffer.BlockCopy(bytes, insertOffset, patched, insertOffset + records.Length, bytes.Length - insertOffset);
        PatchWmfHeaderSizes(patched, wmfOffset);
        Log($"patched SDK WMF window records xExt={windowX}, yExt={windowY}");
        return patched;
    }

    /// <summary>
    /// Return the start of the standard WMF header, after any placeable header.
    /// </summary>
    private static bool TryGetWmfHeaderOffset(byte[] bytes, out int wmfOffset)
    {
        wmfOffset = HasPlaceableHeader(bytes) ? PLACEABLE_WMF_HEADER_SIZE : 0;
        return bytes.Length >= wmfOffset + WMF_HEADER_SIZE;
    }

    /// <summary>
    /// Return whether a WMF record stream contains a given record type.
    /// </summary>
    private static bool WmfHasRecord(byte[] bytes, int wmfOffset, ushort function)
    {
        foreach (var record in EnumerateWmfRecords(bytes, wmfOffset))
        {
            if (record.Function == function)
            {
                return true;
            }
        }
        return false;
    }

    /// <summary>
    /// Choose window extents from the placeable bounds, falling back to SDK dims.
    /// </summary>
    private static (short X, short Y) WmfWindowExtents(byte[] bytes, int wmfOffset, int fallbackXExt, int fallbackYExt)
    {
        if (wmfOffset == PLACEABLE_WMF_HEADER_SIZE)
        {
            var left = BitConverter.ToInt16(bytes, 6);
            var top = BitConverter.ToInt16(bytes, 8);
            var right = BitConverter.ToInt16(bytes, 10);
            var bottom = BitConverter.ToInt16(bytes, 12);
            var xExt = right - left;
            var yExt = bottom - top;
            if (xExt != 0 && yExt != 0)
            {
                return (ClampPositiveInt16(xExt), ClampPositiveInt16(yExt));
            }
        }
        return (ClampPositiveInt16(fallbackXExt), ClampPositiveInt16(fallbackYExt));
    }

    /// <summary>
    /// Insert window records after SETTEXTALIGN, matching MathType OLE previews.
    /// </summary>
    private static int FindWindowRecordInsertOffset(byte[] bytes, int wmfOffset)
    {
        foreach (var record in EnumerateWmfRecords(bytes, wmfOffset))
        {
            if (record.Function == META_SETTEXTALIGN_FUNCTION)
            {
                return record.Offset + checked((int)record.SizeWords * 2);
            }
        }
        return wmfOffset + WMF_HEADER_SIZE;
    }

    /// <summary>
    /// Build SETWINDOWORG and SETWINDOWEXT records for the WMF record stream.
    /// </summary>
    private static byte[] BuildSetWindowRecords(short xExt, short yExt)
    {
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream);
        WriteWmfShortRecord(writer, META_SETWINDOWORG_FUNCTION, 0, 0);
        // WMF SETWINDOWEXT stores parameters as yExt, xExt.
        WriteWmfShortRecord(writer, META_SETWINDOWEXT_FUNCTION, yExt, xExt);
        return stream.ToArray();
    }

    /// <summary>
    /// Write one two-parameter WMF record.
    /// </summary>
    private static void WriteWmfShortRecord(BinaryWriter writer, ushort function, short first, short second)
    {
        writer.Write(5u);
        writer.Write(function);
        writer.Write(first);
        writer.Write(second);
    }

    /// <summary>
    /// Recalculate standard WMF header sizes after inserting records.
    /// </summary>
    private static void PatchWmfHeaderSizes(byte[] bytes, int wmfOffset)
    {
        BitConverter.GetBytes((uint)((bytes.Length - wmfOffset) / 2)).CopyTo(bytes, wmfOffset + 6);
        var maxRecordSize = EnumerateWmfRecords(bytes, wmfOffset)
            .Select(record => record.SizeWords)
            .DefaultIfEmpty(0u)
            .Max();
        BitConverter.GetBytes(maxRecordSize).CopyTo(bytes, wmfOffset + 12);
    }

    /// <summary>
    /// Enumerate WMF records from a standard WMF header.
    /// </summary>
    private static IEnumerable<WmfRecord> EnumerateWmfRecords(byte[] bytes, int wmfOffset)
    {
        var offset = wmfOffset + WMF_HEADER_SIZE;
        while (offset + 6 <= bytes.Length)
        {
            var sizeWords = BitConverter.ToUInt32(bytes, offset);
            var function = BitConverter.ToUInt16(bytes, offset + 4);
            if (sizeWords < 3)
            {
                yield break;
            }

            var recordBytes = checked((int)sizeWords * 2);
            if (offset + recordBytes > bytes.Length)
            {
                yield break;
            }

            yield return new WmfRecord(offset, sizeWords, function);
            if (function == 0)
            {
                yield break;
            }
            offset += recordBytes;
        }
    }

    private static short ClampPositiveInt16(int value)
    {
        // .NET Framework does not provide Math.Clamp. Keep the placeable WMF
        // bounds valid while preserving the previous clamping behavior.
        var magnitude = Math.Abs((long)value);
        if (magnitude < 1)
        {
            return 1;
        }
        if (magnitude > short.MaxValue)
        {
            return short.MaxValue;
        }
        return (short)magnitude;
    }

    private static ushort ComputePlaceableChecksum(byte[] header)
    {
        ushort checksum = 0;
        for (var offset = 0; offset < 20; offset += 2)
        {
            checksum ^= BitConverter.ToUInt16(header, offset);
        }
        return checksum;
    }

    private static byte[] EncodeText(string text, string encodingName)
    {
        var encoding = encodingName.Equals("utf16le", StringComparison.OrdinalIgnoreCase)
            ? Encoding.Unicode
            : Encoding.UTF8;
        var payload = encoding.GetBytes(text);
        var terminator = encodingName.Equals("utf16le", StringComparison.OrdinalIgnoreCase)
            ? new byte[] { 0, 0 }
            : new byte[] { 0 };
        return payload.Concat(terminator).ToArray();
    }

    private static IntPtr CopyToHGlobal(byte[] bytes)
    {
        var handle = GlobalAlloc(0x0042, (UIntPtr)bytes.Length);
        if (handle == IntPtr.Zero)
        {
            throw new OutOfMemoryException("GlobalAlloc failed");
        }

        var locked = GlobalLock(handle);
        if (locked == IntPtr.Zero)
        {
            GlobalFree(handle);
            throw new InvalidOperationException("GlobalLock failed");
        }
        try
        {
            Marshal.Copy(bytes, 0, locked, bytes.Length);
        }
        finally
        {
            GlobalUnlock(handle);
        }

        return handle;
    }

    private static void OleCheck(int hr, string call)
    {
        if (hr < 0)
        {
            Marshal.ThrowExceptionForHR(hr);
        }
        if (hr != S_OK)
        {
            Log($"{call} returned 0x{hr:X8}");
        }
    }

    internal static void Log(string message)
    {
        // Keep normal conversion output quiet; enable this only when debugging
        // noisy OLE/COM transitions on a local MathType installation.
        if (!VerboseLoggingEnabled())
        {
            return;
        }
        Console.Error.WriteLine("[ole-helper] " + message);
        Console.Error.Flush();
    }

    private static bool VerboseLoggingEnabled()
    {
        // Any non-empty, non-zero value restores the old diagnostic stream.
        var value = Environment.GetEnvironmentVariable("MATHTYPE_OLE_HELPER_VERBOSE");
        return !string.IsNullOrWhiteSpace(value) && value != "0";
    }

    private static void MtCheck(int status, string call)
    {
        if (status != MT_OK)
        {
            throw new InvalidOperationException($"{call} returned MathType status {status}");
        }
    }

    [DllImport("ole32.dll")]
    private static extern int OleInitialize(IntPtr pvReserved);

    [DllImport("ole32.dll")]
    private static extern void OleUninitialize();

    [DllImport("ole32.dll", CharSet = CharSet.Unicode)]
    private static extern int CLSIDFromProgID(string lpszProgID, out Guid pclsid);

    [DllImport("ole32.dll", CharSet = CharSet.Unicode)]
    private static extern int StgCreateDocfile(
        string pwcsName,
        int grfMode,
        int reserved,
        out IStorage ppstgOpen);

    [DllImport("ole32.dll")]
    private static extern int OleCreate(
        ref Guid rclsid,
        ref Guid riid,
        uint renderopt,
        IntPtr pFormatEtc,
        IOleClientSite? pClientSite,
        IStorage pStg,
        [MarshalAs(UnmanagedType.Interface)] out object ppvObj);

    [DllImport("ole32.dll")]
    private static extern int OleSave(IPersistStorage pPS, IStorage pStg, bool fSameAsLoad);

    [DllImport("ole32.dll")]
    private static extern int OleRun([MarshalAs(UnmanagedType.IUnknown)] object pUnknown);

    [DllImport("ole32.dll")]
    private static extern void ReleaseStgMedium(ref STGMEDIUM pmedium);

    [DllImport("gdi32.dll")]
    private static extern uint GetMetaFileBitsEx(IntPtr hmf, uint cbBuffer, IntPtr lpData);

    [DllImport("gdi32.dll")]
    private static extern bool DeleteMetaFile(IntPtr hmf);

    [DllImport("ole32.dll")]
    private static extern int WriteClassStg(IStorage pStg, ref Guid rclsid);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern ushort RegisterClipboardFormat(string lpszFormat);

    [DllImport("MT6.dll", CharSet = CharSet.Ansi)]
    private static extern int MTAPIConnect(short options, short timeout);

    [DllImport("MT6.dll", CharSet = CharSet.Ansi)]
    private static extern int MTAPIDisconnect();

    [DllImport("MT6.dll", CharSet = CharSet.Ansi)]
    private static extern int MTGetPrefsFromFile(
        [MarshalAs(UnmanagedType.LPStr)] string prefFile,
        [MarshalAs(UnmanagedType.LPStr)] StringBuilder? prefs,
        short prefsLen);

    [DllImport("MT6.dll", CharSet = CharSet.Ansi)]
    private static extern int MTSetMTPrefs(
        short mode,
        [MarshalAs(UnmanagedType.LPStr)] string prefs,
        short timeout);

    [DllImport("MT6.dll", CharSet = CharSet.Ansi)]
    private static extern int MTGetLastDimension(int dimIndex);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetDllDirectory(string lpPathName);

    [DllImport("kernel32.dll")]
    private static extern IntPtr GlobalAlloc(uint uFlags, UIntPtr dwBytes);

    [DllImport("kernel32.dll")]
    private static extern IntPtr GlobalLock(IntPtr hMem);

    [DllImport("kernel32.dll")]
    private static extern bool GlobalUnlock(IntPtr hMem);

    [DllImport("kernel32.dll")]
    private static extern IntPtr GlobalFree(IntPtr hMem);

    private sealed class Options
    {
        public Options(
            string format,
            string? inputPath,
            string? outputPath,
            string encodingName,
            bool binaryInput,
            bool doVerb,
            string method,
            int preVerb,
            string? batchManifestPath,
            string? prefsFilePath,
            string? previewOutputPath,
            string? metadataOutputPath)
        {
            Format = format;
            InputPath = inputPath;
            OutputPath = outputPath;
            EncodingName = encodingName;
            BinaryInput = binaryInput;
            DoVerb = doVerb;
            Method = method;
            PreVerb = preVerb;
            BatchManifestPath = batchManifestPath;
            PrefsFilePath = prefsFilePath;
            PreviewOutputPath = previewOutputPath;
            MetadataOutputPath = metadataOutputPath;
        }

        public string Format { get; }
        public string? InputPath { get; }
        public string? OutputPath { get; }
        public string EncodingName { get; }
        public bool BinaryInput { get; }
        public bool DoVerb { get; }
        public string Method { get; }
        public int PreVerb { get; }
        public string? BatchManifestPath { get; }
        public string? PrefsFilePath { get; }
        public string? PreviewOutputPath { get; }
        public string? MetadataOutputPath { get; }

        /// <summary>
        /// Reuse shared CLI settings for one manifest entry so a batch still follows the same conversion path.
        /// </summary>
        public Options WithJob(
            string inputPath,
            string outputPath,
            string? previewOutputPath,
            string? metadataOutputPath)
        {
            return new Options(
                Format,
                inputPath,
                outputPath,
                EncodingName,
                BinaryInput,
                DoVerb,
                Method,
                PreVerb,
                null,
                PrefsFilePath,
                previewOutputPath,
                metadataOutputPath);
        }

        public static Options Parse(string[] args)
        {
            string? format = null;
            string? input = null;
            string? output = null;
            string? batchManifest = null;
            var encoding = "utf8";
            var binary = false;
            var doVerb = true;
            var method = "set-data";
            // MathType probing and conversion in this repo are only validated
            // with DoVerb(2), so make it the unconditional default.
            int preVerb = 2;
            string? prefsFilePath = null;
            string? previewOutput = null;
            string? metadataOutput = null;

            for (var i = 0; i < args.Length; i++)
            {
                switch (args[i])
                {
                    case "--format":
                        format = args[++i];
                        break;
                    case "--input":
                        input = args[++i];
                        break;
                    case "--output":
                        output = args[++i];
                        break;
                    case "--batch-manifest":
                        batchManifest = args[++i];
                        break;
                    case "--encoding":
                        encoding = args[++i];
                        break;
                    case "--binary":
                        binary = true;
                        break;
                    case "--no-verb":
                        doVerb = false;
                        break;
                    case "--method":
                        method = args[++i];
                        break;
                    case "--pre-verb":
                        // Keep accepting the flag for explicitness, but reject
                        // any value other than the validated DoVerb(2) path.
                        preVerb = 2;
                        if (i + 1 < args.Length && !args[i + 1].StartsWith("--", StringComparison.Ordinal))
                        {
                            preVerb = RequirePreVerbTwo(int.Parse(args[++i], CultureInfo.InvariantCulture));
                        }
                        break;
                    case "--prefs-file":
                        prefsFilePath = args[++i];
                        break;
                    case "--preview-output":
                        previewOutput = args[++i];
                        break;
                    case "--metadata-output":
                        metadataOutput = args[++i];
                        break;
                    default:
                        throw new ArgumentException($"Unknown argument: {args[i]}");
                }
            }

            if (format is null)
            {
                throw new ArgumentException(GetUsage());
            }

            if (batchManifest is not null)
            {
                if (input is not null || output is not null || previewOutput is not null || metadataOutput is not null)
                {
                    throw new ArgumentException("--batch-manifest cannot be combined with --input, --output, --preview-output, or --metadata-output.");
                }
            }
            else if (input is null || output is null)
            {
                throw new ArgumentException(GetUsage());
            }

            return new Options(
                format ?? "",
                input,
                output,
                encoding,
                binary,
                doVerb,
                method,
                preVerb,
                batchManifest,
                prefsFilePath,
                previewOutput,
                metadataOutput);
        }

        /// <summary>
        /// Batch parsing leaves input unset on the root options object, so conversion paths must ask for it explicitly.
        /// </summary>
        public string GetRequiredInputPath()
        {
            return InputPath ?? throw new InvalidOperationException("input path is required for conversion");
        }

        /// <summary>
        /// Batch parsing leaves output unset on the root options object, so conversion paths must ask for it explicitly.
        /// </summary>
        public string GetRequiredOutputPath()
        {
            return OutputPath ?? throw new InvalidOperationException("output path is required for conversion");
        }

        private static string GetUsage()
        {
            return "Usage: MathTypeOleHelper --format <clipboard format> (--input <file-or-tex-or-mtef> --output <ole.bin> | --batch-manifest <jobs.json>) [--encoding utf8|utf16le] [--binary] [--no-verb] [--method set-data|sdk-xform-ole] [--pre-verb 2] [--prefs-file <eqp>] [--preview-output <wmf>] [--metadata-output <json>]";
        }

        /// <summary>
        /// This helper only supports the pre-open verb sequence validated with MathType DoVerb(2).
        /// </summary>
        private static int RequirePreVerbTwo(int preVerb)
        {
            if (preVerb != 2)
            {
                throw new ArgumentException("--pre-verb only supports value 2.");
            }

            return 2;
        }
    }

    [DataContract]
    private sealed class BatchJob
    {
        /// <summary>
        /// One manifest entry describes one formula conversion request inside a shared helper process.
        /// </summary>
        [DataMember(Name = "input")]
        public string? Input { get; set; }

        [DataMember(Name = "output")]
        public string? Output { get; set; }

        [DataMember(Name = "previewOutput")]
        public string? PreviewOutput { get; set; }

        [DataMember(Name = "metadataOutput")]
        public string? MetadataOutput { get; set; }
    }

    private sealed class Payload
    {
        public Payload(ushort formatId, byte[] bytes)
        {
            FormatId = formatId;
            Bytes = bytes;
        }

        public ushort FormatId { get; }
        public byte[] Bytes { get; }
    }

    private sealed class WmfRecord
    {
        public WmfRecord(int offset, uint sizeWords, ushort function)
        {
            Offset = offset;
            SizeWords = sizeWords;
            Function = function;
        }

        public int Offset { get; }
        public uint SizeWords { get; }
        public ushort Function { get; }
    }

    private sealed class MathTypeLastDimensions
    {
        public MathTypeLastDimensions(
            int widthRaw,
            int heightRaw,
            int baselineRaw,
            int horizPosType,
            int horizPos)
        {
            WidthRaw = widthRaw;
            HeightRaw = heightRaw;
            BaselineRaw = baselineRaw;
            HorizPosType = horizPosType;
            HorizPos = horizPos;
        }

        public int WidthRaw { get; }
        public int HeightRaw { get; }
        public int BaselineRaw { get; }
        public int HorizPosType { get; }
        public int HorizPos { get; }

        private static double ToPoints(int value) => value / MATH_TYPE_DIMENSION_UNITS_PER_POINT;

        public string ToJson() => FormattableString.Invariant(
            $"{{\"width_raw\":{WidthRaw},\"height_raw\":{HeightRaw},\"baseline_from_bottom_raw\":{BaselineRaw},\"width_pt\":{ToPoints(WidthRaw):F4},\"height_pt\":{ToPoints(HeightRaw):F4},\"baseline_from_bottom_pt\":{ToPoints(BaselineRaw):F4},\"horiz_pos_type\":{HorizPosType},\"horiz_pos\":{HorizPos}}}");
    }

    private sealed class PreviewMetadata
    {
        public PreviewMetadata(
            int mapMode,
            int xExt,
            int yExt,
            ushort unitsPerInch,
            double widthPt,
            double heightPt,
            MathTypeLastDimensions? mathType)
        {
            MapMode = mapMode;
            XExt = xExt;
            YExt = yExt;
            UnitsPerInch = unitsPerInch;
            WidthPt = widthPt;
            HeightPt = heightPt;
            MathType = mathType;
        }

        public int MapMode { get; }
        public int XExt { get; }
        public int YExt { get; }
        public ushort UnitsPerInch { get; }
        public double WidthPt { get; }
        public double HeightPt { get; }
        public MathTypeLastDimensions? MathType { get; }

        public string ToJson()
        {
            var mathTypeJson = MathType is null ? "null" : MathType.ToJson();
            return FormattableString.Invariant(
                $"{{\"map_mode\":{MapMode},\"x_ext\":{XExt},\"y_ext\":{YExt},\"units_per_inch\":{UnitsPerInch},\"width_pt\":{WidthPt:F4},\"height_pt\":{HeightPt:F4},\"mathtype\":{mathTypeJson}}}");
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SdkPictResult
    {
        public int MapMode;
        public int XExt;
        public int YExt;
        public IntPtr HMetaFile;
    }
}

[ComImport]
[Guid("00000112-0000-0000-C000-000000000046")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IOleObject
{
    void SetClientSite(IOleClientSite? pClientSite);
    void GetClientSite(out IOleClientSite? ppClientSite);
    void SetHostNames([MarshalAs(UnmanagedType.LPWStr)] string szContainerApp, [MarshalAs(UnmanagedType.LPWStr)] string szContainerObj);
    void Close(uint dwSaveOption);
    void SetMoniker(uint dwWhichMoniker, object? pmk);
    void GetMoniker(uint dwAssign, uint dwWhichMoniker, out object? ppmk);
    void InitFromData(System.Runtime.InteropServices.ComTypes.IDataObject pDataObject, bool fCreation, uint dwReserved);
    void GetClipboardData(uint dwReserved, out System.Runtime.InteropServices.ComTypes.IDataObject ppDataObject);
    void DoVerb(int iVerb, IntPtr lpmsg, IOleClientSite? pActiveSite, int lindex, IntPtr hwndParent, ref RECT lprcPosRect);
    void EnumVerbs(out object ppEnumOleVerb);
    void Update();
    void IsUpToDate();
    void GetUserClassID(out Guid pClsid);
    void GetUserType(uint dwFormOfType, out IntPtr pszUserType);
    void SetExtent(uint dwDrawAspect, ref SIZEL psizel);
    void GetExtent(uint dwDrawAspect, out SIZEL psizel);
    void Advise(IAdviseSink pAdvSink, out int pdwConnection);
    void Unadvise(int dwConnection);
    void EnumAdvise(out object ppenumAdvise);
    void GetMiscStatus(uint dwAspect, out uint pdwStatus);
    void SetColorScheme(IntPtr pLogpal);
}

[ComImport]
[Guid("00000118-0000-0000-C000-000000000046")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IOleClientSite
{
    [PreserveSig]
    int SaveObject();
    [PreserveSig]
    // These outputs are COM interface pointers, not VARIANTs. The default
    // marshalling for out object would overwrite the native caller's memory.
    int GetMoniker(uint dwAssign, uint dwWhichMoniker, [MarshalAs(UnmanagedType.Interface)] out object? ppmk);
    [PreserveSig]
    int GetContainer([MarshalAs(UnmanagedType.Interface)] out object? ppContainer);
    [PreserveSig]
    int ShowObject();
    [PreserveSig]
    int OnShowWindow(bool fShow);
    [PreserveSig]
    int RequestNewObjectLayout();
}

[ComVisible(true)]
internal sealed class MinimalOleClientSite : IOleClientSite
{
    public int SaveObject() => S_OK;
    public int GetMoniker(uint dwAssign, uint dwWhichMoniker, out object? ppmk)
    {
        ppmk = null;
        return E_NOTIMPL;
    }
    public int GetContainer(out object? ppContainer)
    {
        ppContainer = null;
        return E_NOTIMPL;
    }
    public int ShowObject() => S_OK;
    public int OnShowWindow(bool fShow) => S_OK;
    public int RequestNewObjectLayout() => E_NOTIMPL;

    private const int S_OK = 0;
    private const int E_NOTIMPL = unchecked((int)0x80004001);
}

[StructLayout(LayoutKind.Sequential)]
internal struct RECT
{
    public int left;
    public int top;
    public int right;
    public int bottom;
}

[StructLayout(LayoutKind.Sequential)]
internal struct SIZEL
{
    public int cx;
    public int cy;
}

[ComImport]
[Guid("0000000B-0000-0000-C000-000000000046")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IStorage
{
    [PreserveSig]
    int CreateStream([MarshalAs(UnmanagedType.LPWStr)] string pwcsName, int grfMode, int reserved1, int reserved2, out IStream ppstm);
    [PreserveSig]
    int OpenStream([MarshalAs(UnmanagedType.LPWStr)] string pwcsName, IntPtr reserved1, int grfMode, int reserved2, out IStream ppstm);
    [PreserveSig]
    int CreateStorage([MarshalAs(UnmanagedType.LPWStr)] string pwcsName, int grfMode, int reserved1, int reserved2, out IStorage ppstg);
    [PreserveSig]
    int OpenStorage([MarshalAs(UnmanagedType.LPWStr)] string pwcsName, IStorage? pstgPriority, int grfMode, IntPtr snbExclude, int reserved, out IStorage ppstg);
    [PreserveSig]
    int CopyTo(int ciidExclude, IntPtr rgiidExclude, IntPtr snbExclude, IStorage pstgDest);
    [PreserveSig]
    int MoveElementTo([MarshalAs(UnmanagedType.LPWStr)] string pwcsName, IStorage pstgDest, [MarshalAs(UnmanagedType.LPWStr)] string pwcsNewName, int grfFlags);
    [PreserveSig]
    int Commit(int grfCommitFlags);
    [PreserveSig]
    int Revert();
    [PreserveSig]
    int EnumElements(int reserved1, IntPtr reserved2, int reserved3, out object ppenum);
    [PreserveSig]
    int DestroyElement([MarshalAs(UnmanagedType.LPWStr)] string pwcsName);
    [PreserveSig]
    int RenameElement([MarshalAs(UnmanagedType.LPWStr)] string pwcsOldName, [MarshalAs(UnmanagedType.LPWStr)] string pwcsNewName);
    [PreserveSig]
    int SetElementTimes([MarshalAs(UnmanagedType.LPWStr)] string pwcsName, IntPtr pctime, IntPtr patime, IntPtr pmtime);
    [PreserveSig]
    int SetClass(ref Guid clsid);
    [PreserveSig]
    int SetStateBits(int grfStateBits, int grfMask);
    [PreserveSig]
    int Stat(out System.Runtime.InteropServices.ComTypes.STATSTG pstatstg, int grfStatFlag);
}

[ComImport]
[Guid("0000010A-0000-0000-C000-000000000046")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IPersistStorage
{
    void GetClassID(out Guid pClassID);
    [PreserveSig]
    int IsDirty();
    void InitNew(IStorage pStg);
    void Load(IStorage pStg);
    void Save(IStorage pStgSave, bool fSameAsLoad);
    void SaveCompleted(IStorage pStgNew);
    void HandsOffStorage();
}
