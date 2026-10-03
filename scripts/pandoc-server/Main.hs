{-
  Pandoc-compatible CLI and long-lived worker for the PMT HTML server.

  Build with `cargo run -p papper-dev -- worker`, which prepares pinned Pandoc
  sources and Papper's supported format registries before invoking Cabal.
  Ordinary arguments use the Pandoc 3.12 CLI within that format profile;
  --pmt-worker --config PATH starts the private worker.
  JSON lines specify an input snapshot, output, and dependency fingerprint.
  Pandoc and crossref run in this process; the citation adapter caches CSL,
  references, and evaluated citations while applying Pandoc's document
  mutations to fresh prose on every request. Changed assets reload defaults.

  CLI dispatch is adapted from Pandoc 3.12's pandoc-cli/src/pandoc.hs,
  Copyright (C) 2006-2024 John MacFarlane, GPL-2.0-or-later.
-}

{-# LANGUAGE DeriveGeneric #-}
{-# LANGUAGE CPP #-}
{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE RankNTypes #-}

module Main where

import Control.Exception (SomeException, evaluate, try)
import qualified Control.Exception as E
import Control.DeepSeq (force)
import Control.Monad.IO.Class (liftIO)
import Control.Applicative ((<|>))
import Data.Time.Clock (diffUTCTime, getCurrentTime)
import qualified Data.Text as T
import qualified Data.Map as M
import Data.Maybe (fromMaybe)
import Data.Aeson (FromJSON, Value, eitherDecode, encode, object, (.=))
import qualified Data.Aeson.Key as Key
import qualified Data.ByteString.Char8 as B8
import qualified Data.ByteString.Lazy.Char8 as BL8
import GHC.Generics (Generic)
import Data.IORef (IORef, newIORef, modifyIORef', readIORef, writeIORef)
import Text.Pandoc.Definition (Pandoc (..), Format (..))
import Text.Pandoc.CrossRef (runCrossRefIO, defaultCrossRefAction)
import qualified Papper.Citeproc as Citations
import Text.Pandoc.Filter (Filter (..))
import System.Directory (canonicalizePath, createDirectoryIfMissing, makeAbsolute)
import System.Environment (getArgs, getProgName, setEnv, unsetEnv, withProgName)
import qualified System.Info as System
import System.FilePath (isRelative, makeRelative, normalise, takeBaseName, takeDirectory, splitDirectories, (</>))
import System.IO (BufferMode (LineBuffering), hSetBuffering, stdin, stdout)
import Text.Pandoc.App (Opt (..), convertWithOpts,
                        defaultOpts, options, parseOptionsFromArgs, handleOptInfo, versionInfo)
import Text.Pandoc.Error (handleError)
import PandocCLI.Lua (getEngine, runLuaInterpreter)
import PandocCLI.Server (runCGI, runServer)
import Text.Pandoc.Scripting (ScriptingEngine (..))

data Config = Config
  { pandocArgs :: [String]
  , projectDir :: FilePath
  , workDirs :: Maybe [FilePath]
  } deriving (Generic, Show)

instance FromJSON Config

data Request = Request
  { input :: FilePath
  , output :: FilePath
  , mode :: Maybe String
  , inputFormat :: Maybe String
  , assetFingerprint :: Maybe String
  , metadataFile :: Maybe FilePath
  , resourcePaths :: Maybe [FilePath]
  , filterEnvironment :: Maybe (M.Map String (Maybe String))
  , luaBundle :: Maybe FilePath
  , luaBundlePaths :: Maybe [FilePath]
  , remoteResources :: Maybe (M.Map String FilePath)
  } deriving (Generic, Show)

instance FromJSON Request

-- | Dispatch the private worker separately from Pandoc's official CLI modes.
main :: IO ()
main = E.handle (handleError . Left) $ do
  args <- getArgs
  case args of
    "--pmt-worker" : workerArgs -> runWorker workerArgs
    -- Retain the old private launch convention for developer worker overrides.
    "--config" : _ -> runWorker args
    ["--pmt-crossref-version"] ->
      putStrLn $ "pandoc-crossref v" ++ VERSION_pandoc_crossref ++ " (embedded)"
    _ -> do
      program <- getProgName
      -- Upstream help and server option handlers read getProgName themselves.
      -- Present the official name so diagnostics do not expose the worker name.
      let cliName = if program `elem` ["pandoc-server.cgi", "pandoc-server", "pandoc-lua"]
            then program
            else if System.os == "mingw32" then "pandoc.exe" else "pandoc"
      withProgName cliName $ runCLI args

-- | Follow Pandoc 3.12's CLI dispatch, changing only standard crossref execution.
runCLI :: [String] -> IO ()
runCLI rawArgs = do
  program <- getProgName
  let hasVersion = any (`elem` ["-v", "--version"]) (takeWhile (/= "--") rawArgs)
      versionAction = do
        engine <- getEngine
        versionInfo ["+server", "+lua"] (Just $ T.unpack $ engineName engine) ""
      versionOr action = if hasVersion then versionAction else action
      convert args = do
        engine <- getEngine
        parsed <- parseOptionsFromArgs options defaultOpts program args
        case parsed of
          Left info -> handleOptInfo engine info
          Right opts -> do
            timings <- newIORef []
            citationCache <- Citations.newCitationCache
            -- CLI citeproc retains Pandoc's native behavior; only the worker
            -- substitutes the adapter that caches citation evaluation.
            convertWithOpts (embeddedEngine engine timings citationCache)
              opts {optFilters = map embedCrossrefFilter (optFilters opts)}
  case program of
    "pandoc-server.cgi" -> versionOr runCGI
    "pandoc-server" -> versionOr $ runServer rawArgs
    "pandoc-lua" -> runLuaInterpreter program rawArgs
    _ -> case rawArgs of
      "lua" : args -> runLuaInterpreter "pandoc lua" args
      "server" : args -> versionOr $ runServer args
      args -> versionOr $ convert args

-- | Initialize one persistent worker and consume its private JSON-line protocol.
runWorker :: [String] -> IO ()
runWorker args = do
  hSetBuffering stdin LineBuffering
  hSetBuffering stdout LineBuffering
  configPath <- argumentValue "--config" args
  config <- decodeFile configPath
  baseOpts <- loadOptions config
  optionsCache <- newIORef (Nothing, baseOpts)
  luaEngine <- getEngine
  timings <- newIORef []
  citationCache <- Citations.newCitationCache
  let engine = embeddedEngine luaEngine timings citationCache
  loop engine timings citationCache config optionsCache

-- | Parse project defaults again only after the dependency generation changes.
loadOptions :: Config -> IO Opt
loadOptions config = do
  parsed <- parseOptionsFromArgs options defaultOpts "pmt-pandoc-worker" (pandocArgs config)
  opts <- either (fail . show) pure parsed
  pure opts {optFilters = map embedFilter (optFilters opts)}

-- Replace only the standard crossref executable; custom JSON filters retain
-- Pandoc's existing subprocess behavior and their original order.
embedFilter :: Filter -> Filter
embedFilter CiteprocFilter = LuaFilter "papper-embedded-citeproc"
embedFilter filt = embedCrossrefFilter filt

-- | Embed the standard crossref filter while preserving explicit custom programs.
embedCrossrefFilter :: Filter -> Filter
embedCrossrefFilter (JSONFilter path)
  | path `elem` ["pandoc-crossref", "pandoc-crossref.exe"] = LuaFilter "papper-embedded-crossref"
embedCrossrefFilter filt = filt

-- Run the crossref library in this process while preserving config-file
-- handling. Force results within the timer because Haskell evaluates lazily.
embeddedEngine :: ScriptingEngine -> IORef [(String, Double)] -> Citations.CitationCache -> ScriptingEngine
embeddedEngine luaEngine timings citationCache = luaEngine
  { engineApplyFilter = \env args path doc@(Pandoc meta _) -> do
      started <- liftIO getCurrentTime
      result <- case takeBaseName path of
        "papper-embedded-crossref" -> liftIO $ runCrossRefIO meta (Format . T.pack <$> first args)
                        defaultCrossRefAction doc
        "papper-embedded-citeproc" -> Citations.processCitations citationCache doc
        _ -> engineApplyFilter luaEngine env args path doc
      strict <- liftIO $ evaluate $ force result
      finished <- liftIO getCurrentTime
      liftIO $ modifyIORef' timings
        (++ [(takeBaseName path, realToFrac (diffUTCTime finished started) * 1000)])
      pure strict
  }

-- Safely obtain the output format passed by Pandoc's filter runner.
first :: [a] -> Maybe a
first [] = Nothing
first (value : _) = Just value

-- | Return one response per line, including recoverable conversion errors.
loop :: ScriptingEngine -> IORef [(String, Double)] -> Citations.CitationCache -> Config -> IORef (Maybe String, Opt) -> IO ()
loop engine timings citationCache config optionsCache = do
  -- Lazy ByteString Char8 has no line reader; convert each strict stdin line.
  line <- BL8.fromStrict <$> B8.getLine
  if BL8.null line
    then pure ()
    else do
      response <- case eitherDecode line of
        Left err -> pure $ object ["ok" .= False, "error" .= err]
        Right request -> convertRequest engine timings citationCache config optionsCache request
      BL8.putStrLn (encode response)
      loop engine timings citationCache config optionsCache

-- | Refresh assets and execute one conversion with wall-clock filter timings.
convertRequest :: ScriptingEngine -> IORef [(String, Double)] -> Citations.CitationCache -> Config -> IORef (Maybe String, Opt) -> Request -> IO Value
convertRequest engine timings citationCache config optionsCache request = do
  started <- getCurrentTime
  writeIORef timings []
  Citations.invalidateCitationCache citationCache (assetFingerprint request)
  Citations.setRemoteResources citationCache (fromMaybe M.empty (remoteResources request))
  result <- try $ do
    (previous, cachedOptions) <- readIORef optionsCache
    baseOpts <- if assetFingerprint request == Nothing || previous /= assetFingerprint request
      then loadOptions config
      else pure cachedOptions
    writeIORef optionsCache (assetFingerprint request, baseOpts)
    mapM_ (mapM_ setFilterVariable . M.toList) (filterEnvironment request)
    source <- resolveProjectPath config (input request)
    target <- resolveProjectPath config (output request)
    createDirectoryIfMissing True (takeDirectory target)
    let opts = requestOptions baseOpts request source target
    convertWithOpts engine opts
  finished <- getCurrentTime
  filterTimings <- readIORef timings
  citationsReused <- Citations.citationCacheHit citationCache
  let elapsedMs :: Int
      elapsedMs = round (realToFrac (diffUTCTime finished started) * 1000.0)
  case result of
    Left err -> pure $ object
      [ "ok" .= False
      , "error" .= show (err :: SomeException)
      , "elapsed_ms" .= elapsedMs
      ]
    Right () -> pure $ object ["ok" .= True, "elapsed_ms" .= elapsedMs,
                              "citeproc_cache_hit" .= citationsReused,
                              "filters_ms" .= object [Key.fromString key .= value | (key, value) <- filterTimings]]

-- | Update only Papper's filter settings; arbitrary environment keys are rejected.
setFilterVariable :: (String, Maybe String) -> IO ()
setFilterVariable (key, value)
  | key == "PMT_CITATION_NUMBER_RANGE_DELIMITER" = maybe (unsetEnv key) (setEnv key) value
  | otherwise = fail $ "Unsupported filter environment variable: " ++ key

-- | Apply per-source metadata/resources and exact/fragment writer semantics.
requestOptions :: Opt -> Request -> FilePath -> FilePath -> Opt
requestOptions baseOpts request source target =
  let prepared = baseOpts
        { optMetadataFiles = maybe (optMetadataFiles baseOpts) (:[]) (metadataFile request)
        , optResourcePath = fromMaybe (optResourcePath baseOpts) (resourcePaths request)
        , optFilters = bundleFilters request (optFilters baseOpts)
        }
  in
  case mode request of
    Just "ast" -> prepared
      { optInputFiles = Just [source]
      , optOutputFile = Just target
      , optFrom = Just "markdown"
      , optTo = Just "json"
      , optStandalone = False
      , optTemplate = Nothing
      , optFilters = []
      , optMetadataFiles = []
      , optCSL = Nothing
      , optBibliography = []
      , optCitationAbbreviations = Nothing
      }

    Just "preview" -> prepared
      { optInputFiles = Just [source]
      , optOutputFile = Just target
      , optFrom = fmap T.pack (inputFormat request) <|> optFrom baseOpts
      , optTo = Just "html"
      , optStandalone = False
      , optTemplate = Nothing
      }
    _ -> prepared
      { optInputFiles = Just [source]
      , optOutputFile = Just target
      }

-- | Replace only an exact contiguous sequence verified by the dependency cache.
-- Extra/reordered/custom filters retain their original invocation boundaries.
bundleFilters :: Request -> [Filter] -> [Filter]
bundleFilters request = go
  where
    expected = map (LuaFilter . normalise) $ fromMaybe [] (luaBundlePaths request)
    -- Windows accepts both slash styles in configured Lua paths.
    normalizeFilter (LuaFilter path) = LuaFilter (normalise path)
    normalizeFilter filt = filt
    go [] = []
    go filters@(filt : rest) = case luaBundle request of
      Just bundle | length expected == 5 && map normalizeFilter (take 5 filters) == expected ->
        LuaFilter bundle : go (drop 5 filters)
      _ -> filt : go rest

-- | Permit project files and explicitly allocated private intermediates only.
resolveProjectPath :: Config -> FilePath -> IO FilePath
resolveProjectPath config raw = do
  root <- canonicalizePath (projectDir config)
  path <- canonicalizePath =<< makeAbsolute (if isRelative raw then root </> raw else raw)
  roots <- mapM canonicalizePath $ root : fromMaybe [] (workDirs config)
  if any (`containsPath` path) roots
    then pure path
    else fail $ "Path is outside the PMT project: " ++ path

-- | Compare path components so `..notes` is allowed but traversal is rejected.
containsPath :: FilePath -> FilePath -> Bool
containsPath root path =
  let relative = makeRelative root path
  in relative == "." || (isRelative relative && case splitDirectories relative of
    ".." : _ -> False
    _ -> True)

-- | Read a required internal CLI option without changing Pandoc's own parser.
argumentValue :: String -> [String] -> IO String
argumentValue name args = case dropWhile (/= name) args of
  (_ : value : _) -> pure value
  _ -> fail $ "Missing " ++ name

-- | Decode the worker configuration before accepting requests.
decodeFile :: FromJSON a => FilePath -> IO a
decodeFile path = do
  contents <- BL8.readFile path
  either fail pure (eitherDecode contents)
