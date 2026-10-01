{-
  Adapted from Pandoc 3.11, src/Text/Pandoc/Citeproc.hs.
  Copyright (C) 2006-2025 John MacFarlane and Pandoc contributors.
  SPDX-License-Identifier: GPL-2.0-or-later
  Upstream: https://github.com/jgm/pandoc/tree/3.11
  See vendor/pandoc/COPYING.md and COPYRIGHT for the upstream license.
-}

{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE FlexibleContexts #-}
{-# LANGUAGE StrictData #-}
{-# LANGUAGE MultiParamTypeClasses #-}
{-# LANGUAGE FlexibleInstances #-}
{-# LANGUAGE OverloadedStrings #-}
module Papper.Citeproc
  ( CitationCache, newCitationCache, invalidateCitationCache,
    citationCacheHit, setRemoteResources, processCitations,
  )
where

import Citeproc
import Text.Pandoc.Citeproc (getReferences)
import Citeproc.Pandoc ()
import Papper.Locator (parseLocator, toLocatorMap,
                                     LocatorInfo(..))
import Text.Pandoc.Builder (Inlines, Many(..), deleteMeta, setMeta)
import qualified Text.Pandoc.Builder as B
import Text.Pandoc.Definition as Pandoc
import Text.Pandoc.Class (PandocMonad(..), getResourcePath, getUserDataDir,
                          fetchItem, report, setResourcePath, toTextM)
import Text.Pandoc.Data (readDataFile)
import Text.Pandoc.Error (PandocError(..))
import Text.Pandoc.Logging (LogMessage(..))
import Text.Pandoc.Shared (stringify, makeSections, blocksToInlines')
import Data.Containers.ListUtils (nubOrd)
import Text.Pandoc.Walk (query, walk, walkM)
import Control.Applicative ((<|>))
import Control.Monad.Except (catchError, throwError)
import Control.Monad.State (State, evalState, get, put, runState)
import Data.Aeson (eitherDecode)
import qualified Data.ByteString.Lazy as L
import Data.Char (isPunctuation, isUpper)
import Data.Default (Default(def))
import qualified Data.Foldable as Foldable
import qualified Data.Map as M
import Data.Maybe (mapMaybe, fromMaybe)
import Data.Ord ()
import qualified Data.Sequence as Seq
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as T
import Safe (lastMay, initSafe)
import Control.Monad.IO.Class (MonadIO, liftIO)
import Control.DeepSeq (force)
import Control.Exception (evaluate)
import Data.IORef (IORef, newIORef, readIORef, writeIORef)

-- | Bounded caches of citeproc inputs; document text is never cached here.
data CitationCache = CitationCache
  { generation :: IORef (Maybe String)
  , styles :: IORef [(Meta, Style Inlines)]
  , references :: IORef [((Meta, Set.Set Text), [Reference Inlines])]
  , results :: IORef [((Meta, [Citeproc.Citation Inlines]), Result Inlines)]
  , lastResultHit :: IORef Bool
  , remoteResources :: IORef (M.Map Text Text)
  }

-- | Allocate caches owned by one serialized Pandoc worker.
newCitationCache :: IO CitationCache
newCitationCache = CitationCache <$> newIORef Nothing <*> newIORef []
                                <*> newIORef [] <*> newIORef [] <*> newIORef False
                                <*> newIORef M.empty

-- | Use exact URL mappings so unrelated CSL styles with equal basenames
-- cannot shadow each other's remotely cached parent styles.
setRemoteResources :: CitationCache -> M.Map String String -> IO ()
setRemoteResources cache = writeIORef (remoteResources cache) . M.map T.pack . M.mapKeys T.pack

-- | Drop prepared assets/results when dependencies change. Legacy clients
-- without a fingerprint must recompute because their files may have changed.
invalidateCitationCache :: CitationCache -> Maybe String -> IO ()
invalidateCitationCache cache fingerprint = do
  previous <- readIORef (generation cache)
  if fingerprint == Nothing || previous /= fingerprint
    then do
      writeIORef (styles cache) []
      writeIORef (references cache) []
      writeIORef (results cache) []
      writeIORef (generation cache) fingerprint
    else pure ()
  writeIORef (lastResultHit cache) False

-- | Report reuse of citation evaluation separately from whole-HTML reuse.
citationCacheHit :: CitationCache -> IO Bool
citationCacheHit = readIORef . lastResultHit

-- | Keep a small MRU list; equality checks actual semantic inputs, not hashes.
memoize :: (MonadIO m, Eq k) => IORef [(k, v)] -> k -> m v -> m v
memoize entries key action = do
  previous <- liftIO $ readIORef entries
  value <- maybe action pure $ lookup key previous
  liftIO $ writeIORef entries $ take 8 $ (key, value) : filter ((/= key) . fst) previous
  pure value

-- | Extract IDs from real and nocite citations before reference filtering.
citedIds :: Inline -> Set.Set Text
citedIds (Cite cs _) = Set.fromList $ map Pandoc.citationId cs
citedIds _ = mempty

-- | Process citations using Pandoc's citation semantics.
processCitations  :: (PandocMonad m, MonadIO m) => CitationCache -> Pandoc -> m Pandoc
processCitations cache (Pandoc meta bs) = do
  assets <- liftIO $ readIORef (remoteResources cache)
  style <- memoize (styles cache) meta $ getStyle assets (Pandoc meta bs)
  mblang <- getCiteprocLang meta
  let locale = Citeproc.mergeLocales mblang style

  let addQuoteSpan (Quoted _ xs) = Span ("",["csl-quoted"],[]) xs
      addQuoteSpan x = x
  refs <- memoize (references cache) (meta, query citedIds (Pandoc meta bs)) $
          map (walk addQuoteSpan) <$> getReferences (Just locale) (Pandoc (localBibliographies assets meta) bs)

  let otherIdsMap = foldr (\ref m ->
                             case T.words . extractText <$>
                                  M.lookup "other-ids"
                                      (referenceVariables ref) of
                                Nothing  -> m
                                Just ids -> foldr
                                  (\id' ->
                                    M.insert id' (referenceId ref)) m ids)
                          M.empty refs
  let meta' = deleteMeta "nocite" meta
  let citations = getCitations locale otherIdsMap $ Pandoc meta' bs


  let linkCites = maybe False truish (lookupMeta "link-citations" meta) &&
                  -- don't link citations if no bibliography to link to:
             not (maybe False truish (lookupMeta "suppress-bibliography" meta))
  let linkBib = maybe True truish $ lookupMeta "link-bibliography" meta
  let opts = defaultCiteprocOptions{ linkCitations = linkCites
                                   , linkBibliography = linkBib }
  previousResults <- liftIO $ readIORef (results cache)
  liftIO $ writeIORef (lastResultHit cache) $ case lookup (meta, citations) previousResults of
    Just _ -> True
    Nothing -> False
  result <- memoize (results cache) (meta, citations) $ do
    let computed = Citeproc.citeproc opts style mblang refs citations
    -- Materialize the cache value before publication so a failed evaluation
    -- cannot poison subsequent requests with an unevaluated failing thunk.
    _ <- liftIO $ evaluate $ force
      (map B.toList (resultCitations computed),
       map (\(ident, out) -> (ident, B.toList out)) (resultBibliography computed),
       resultWarnings computed)
    pure computed
  mapM_ (report . CiteprocWarning) (resultWarnings result)
  let sopts = styleOptions style
  let classes = "references" : -- TODO remove this or keep for compatibility?
                "csl-bib-body" :
                ["hanging-indent" | styleHangingIndent sopts]
  let refkvs = (case styleEntrySpacing sopts of
                   Just es -> (("entry-spacing",T.pack $ show es):)
                   _ -> id) .
               (case styleLineSpacing sopts of
                   Just ls | ls > 1 -> (("line-spacing",T.pack $ show ls):)
                   _ -> id) $ []
  let bibs = mconcat $ map (\(ident, out) ->
                     B.divWith ("ref-" <> ident,["csl-entry"],[]) . B.para .
                         insertSpace $ out)
                      (resultBibliography result)
  let moveNotes = maybe (styleIsNoteStyle sopts) truish
                   (lookupMeta "notes-after-punctuation" meta)
  let cits = resultCitations result

  let metanocites = lookupMeta "nocite" meta
  let Pandoc meta'' bs' =
         maybe id (setMeta "nocite") metanocites .
         walk (mvPunct moveNotes locale) .
         (if styleIsNoteStyle sopts
             then walk addNote .  walk deNote
             else id) .
         evalState (walkM insertResolvedCitations $ Pandoc meta' bs)
         $ cits
  return $ walk removeQuoteSpan
         $ insertRefs refkvs classes (B.toList bibs)
         $ Pandoc meta'' bs'

-- | Remove quote span using Pandoc's citation semantics.
removeQuoteSpan :: Inline -> Inline
removeQuoteSpan (Span ("",["csl-quoted"],[]) xs) = Span nullAttr xs
removeQuoteSpan x = x

-- | Retrieve the CSL style specified by the csl or citation-style
-- metadata field in a pandoc document, or the default CSL style
-- if none is specified.  Retrieve the parent style
-- if the style is a dependent style.  Add abbreviations defined
-- in an abbreviation file if one has been specified.
-- | Get style using Pandoc's citation semantics.
getStyle :: PandocMonad m => M.Map Text Text -> Pandoc -> m (Style Inlines)
getStyle assets (Pandoc meta _) = do
  let cslfile = (lookupMeta "csl" meta <|> lookupMeta "citation-style" meta)
                >>= metaValueToText

  let getFile defaultExtension fp = do
        oldRp <- getResourcePath
        mbUdd <- getUserDataDir
        setResourcePath $ oldRp ++ maybe []
                                   (\u -> [u <> "/csl",
                                           u <> "/csl/dependent"]) mbUdd
        let resolved = M.findWithDefault fp fp assets
        let fp' = if T.any (=='.') resolved || "data:" `T.isPrefixOf` resolved
                     then resolved
                     else resolved <> defaultExtension
        (result, _) <- fetchItem fp'
        setResourcePath oldRp
        return result

  let getCslDefault = readDataFile "default.csl"

  cslContents <- maybe getCslDefault (getFile ".csl") cslfile >>=
                   toTextM (maybe mempty T.unpack cslfile)

  let abbrevFile = lookupMeta "citation-abbreviations" meta >>= metaValueToText

  mbAbbrevs <- case abbrevFile of
                 Nothing -> return Nothing
                 Just fp -> do
                   rawAbbr <- getFile ".json" fp
                   case eitherDecode (L.fromStrict rawAbbr) of
                     Left err -> throwError $ PandocCiteprocError $
                                 CiteprocParseError $
                                 "Could not parse abbreviations file " <> fp
                                 <> "\n" <> T.pack err
                     Right abbr -> return $ Just abbr

  let getParentStyle url = do
        -- first, try to retrieve the style locally, then use HTTP.
        let basename = T.takeWhileEnd (/='/') url
        catchError (getFile ".csl" basename) (\_ -> case M.lookup url assets of
          Just local -> getFile ".csl" local
          Nothing -> fst <$> fetchItem url)
          >>= toTextM (T.unpack url)

  styleRes <- Citeproc.parseStyle getParentStyle cslContents
  case styleRes of
     Left err    -> throwError $ PandocAppError $ prettyCiteprocError err
     Right style -> return style{ styleAbbreviations = mbAbbrevs }

-- | Substitute download snapshots only for bibliography paths; preserve all
-- original document metadata and inline reference values in rendered output.
localBibliographies :: M.Map Text Text -> Meta -> Meta
localBibliographies assets meta =
  maybe meta (\value -> setMeta "bibliography" (replace value) meta) (lookupMeta "bibliography" meta)
  where
    replace (MetaList values) = MetaList (map replace values)
    replace value = case metaValueToText value >>= (`M.lookup` assets) of
      Just local -> MetaString local
      Nothing -> value


-- Retrieve citeproc lang based on metadata.
-- | Get citeproc lang using Pandoc's citation semantics.
getCiteprocLang :: PandocMonad m => Meta -> m (Maybe Lang)
getCiteprocLang meta = maybe (return Nothing) bcp47LangToIETF
  ((lookupMeta "lang" meta <|> lookupMeta "locale" meta) >>= metaValueToText)

-- If we have a span.csl-left-margin followed by span.csl-right-inline,
-- we insert a space. This ensures that they will be separated by a space,
-- even in formats that don't have special handling for the display spans.
-- | Insert space using Pandoc's citation semantics.
insertSpace :: Inlines -> Inlines
insertSpace ils =
  case Seq.viewl (unMany ils) of
    (Span ("",["csl-left-margin"],[]) xs) Seq.:< rest ->
      case Seq.lookup 0 rest of
        Just (Span ("",["csl-right-inline"],[]) _) ->
          Many $
            Span ("",["csl-left-margin"],[]) (xs ++ case lastMay xs of
                                                      Just Space -> []
                                                      _          -> [Space])
            Seq.<| rest
        _ -> ils
    _ -> ils

-- assumes we walk in same order as query
-- | Insert resolved citations using Pandoc's citation semantics.
insertResolvedCitations :: Inline -> State [Inlines] Inline
insertResolvedCitations (Cite cs ils) = do
  resolved <- get
  case resolved of
    [] -> return (Cite cs ils)
    (x:xs) -> do
      put xs
      return $ Cite cs (B.toList x)
insertResolvedCitations x = return x

-- | Get citations using Pandoc's citation semantics.
getCitations :: Locale
             -> M.Map Text ItemId
             -> Pandoc
             -> [Citeproc.Citation Inlines]
getCitations locale otherIdsMap (Pandoc meta blocks) =
  Foldable.toList (query getCitation meta <>
                   foldMap handleBlock (makeSections False Nothing blocks))
 where
  handleBlock :: Block -> Seq.Seq (Citeproc.Citation Inlines)
  handleBlock b@(Div (_,cls,_) _)
    | "section" `elem` cls
    , "reset-citation-positions" `elem` cls =
      case Seq.viewl (query getCitation b) of
        x Seq.:< xs -> addResetTo x Seq.<| xs
        Seq.EmptyL -> mempty
  handleBlock b = query getCitation b
  addResetTo citation = citation{ Citeproc.citationResetPosition = True }
  getCitation (Cite cs _fallback) = Seq.singleton $
    Citeproc.Citation { Citeproc.citationId = Nothing
                      , Citeproc.citationResetPosition = False
                      , Citeproc.citationPrefix = Nothing
                      , Citeproc.citationSuffix = Nothing
                      , Citeproc.citationNoteNumber =
                          case cs of
                            []    -> Nothing
                            (Pandoc.Citation{ Pandoc.citationNoteNum = n }:
                               _) | n > 0     -> Just n
                                  | otherwise -> Nothing
                      , Citeproc.citationItems =
                           fromPandocCitations locale otherIdsMap cs
                      }
  getCitation _ = mempty

-- | From pandoc citations using Pandoc's citation semantics.
fromPandocCitations :: Locale
                    -> M.Map Text ItemId
                    -> [Pandoc.Citation]
                    -> [CitationItem Inlines]
fromPandocCitations locale otherIdsMap = concatMap go
 where
  locmap = toLocatorMap locale
  go c =
    let (mblocinfo, suffix) = parseLocator locmap (Pandoc.citationSuffix c)
        cit = CitationItem
               { citationItemId = fromMaybe
                   (ItemId $ Pandoc.citationId c)
                   (M.lookup (Pandoc.citationId c) otherIdsMap)
               , citationItemLabel = locatorLabel <$> mblocinfo
               , citationItemLocator = locatorLoc <$> mblocinfo
               , citationItemType = NormalCite
               , citationItemPrefix = case Pandoc.citationPrefix c of
                                        [] -> Nothing
                                        ils -> Just $ B.fromList ils <>
                                                      B.space
               , citationItemSuffix = case suffix of
                                        [] -> Nothing
                                        ils -> Just $ B.fromList ils
               , citationItemData = Nothing }
     in if Pandoc.citationId c == "*"
           then []
           else
             case citationMode c of
                  AuthorInText   -> [ cit{ citationItemType = AuthorOnly
                                         , citationItemSuffix = Nothing }
                                    , cit{ citationItemType =
                                              Citeproc.SuppressAuthor
                                         , citationItemPrefix = Nothing } ]
                  NormalCitation -> [ cit ]
                  Pandoc.SuppressAuthor
                                 -> [ cit{ citationItemType =
                                              Citeproc.SuppressAuthor } ]



-- | Is note using Pandoc's citation semantics.
isNote :: Inline -> Bool
isNote (Note _) = True
-- the following allows citation styles that are "in-text" but use superscript
-- references to be treated as if they are "notes" for the purposes of moving
-- the citations after trailing punctuation (see <https://github.com/jgm/pandoc-citeproc/issues/382>):
isNote (Superscript _) = True
isNote _ = False

-- | Is spacy using Pandoc's citation semantics.
isSpacy :: Inline -> Bool
isSpacy Space     = True
isSpacy SoftBreak = True
isSpacy _         = False

-- | Move punct inside quotes using Pandoc's citation semantics.
movePunctInsideQuotes :: Locale -> [Inline] -> [Inline]
movePunctInsideQuotes locale
  | localePunctuationInQuote locale == Just True
    = B.toList . movePunctuationInsideQuotes . B.fromList
  | otherwise
    = id

-- | Mv punct using Pandoc's citation semantics.
mvPunct :: Bool -> Locale -> [Inline] -> [Inline]
mvPunct moveNotes locale (x : xs)
  | isSpacy x = x : mvPunct moveNotes locale xs
-- 'x [^1],' -> 'x,[^1]'
mvPunct moveNotes locale (q : s : x@(Cite _ [il]) : ys)
  | isSpacy s
  , isNote il
  = let spunct = T.takeWhile isPunct $ stringify ys
    in  if moveNotes
           then if T.null spunct
                   then q : x : mvPunct moveNotes locale ys
                   else movePunctInsideQuotes locale
                        [q , Str spunct , x] ++ mvPunct moveNotes locale
                        (B.toList
                          (dropTextWhile isPunct (B.fromList ys)))
           else q : x : mvPunct moveNotes locale ys
-- 'x[^1],' -> 'x,[^1]'
mvPunct moveNotes locale (Cite cs ils@(_:_) : ys)
   | isNote (last ils)
   , startWithPunct ys
   , moveNotes
   = let s = stringify ys
         spunct = T.takeWhile isPunct s
     in  Cite cs (movePunctInsideQuotes locale $
                    init ils
                    ++ [Str spunct | not (endWithPunct False (init ils))]
                    ++ [last ils]) :
         mvPunct moveNotes locale
           (B.toList (dropTextWhile isPunct (B.fromList ys)))
mvPunct moveNotes locale (s : x@(Cite _ [il]) : ys)
  | isSpacy s
  , isNote il
  = x : mvPunct moveNotes locale ys
mvPunct moveNotes locale (s : x@(Cite _ (Superscript _ : _)) : ys)
  | isSpacy s = x : mvPunct moveNotes locale ys
mvPunct moveNotes locale (Cite cs ils : Str "." : ys)
  | "." `T.isSuffixOf` (stringify ils)
  = Cite cs ils : mvPunct moveNotes locale ys
mvPunct moveNotes locale (x:xs) = x : mvPunct moveNotes locale xs
mvPunct _ _ [] = []

-- We don't treat an em-dash or en-dash as punctuation here, because we don't
-- want notes and quotes to move around them.
-- | Is punct using Pandoc's citation semantics.
isPunct :: Char -> Bool
isPunct c = isPunctuation c && c /= '\x2014' && c /= '\x2013'

-- | End with punct using Pandoc's citation semantics.
endWithPunct :: Bool -> [Inline] -> Bool
endWithPunct _ [] = False
endWithPunct onlyFinal xs@(_:_) =
  case reverse (T.unpack $ stringify xs) of
       []                       -> True
       -- covers .), .", etc.:
       (d:c:_) | isPunct d
                 && not onlyFinal
                 && isEndPunct c -> True
       (c:_) | isEndPunct c      -> True
             | otherwise         -> False
  where isEndPunct c = c `elem` (".,;:!?" :: String)



-- | Start with punct using Pandoc's citation semantics.
startWithPunct :: [Inline] -> Bool
startWithPunct ils =
  case T.uncons (stringify ils) of
    Just (c,_) -> c `elem` (".,;:!?" :: [Char])
    Nothing -> False

-- | Truish using Pandoc's citation semantics.
truish :: MetaValue -> Bool
truish (MetaBool t) = t
truish (MetaString s) = isYesValue (T.toLower s)
truish (MetaInlines ils) = isYesValue (T.toLower (stringify ils))
truish (MetaBlocks [Plain ils]) = isYesValue (T.toLower (stringify ils))
truish _ = False

-- | Is yes value using Pandoc's citation semantics.
isYesValue :: Text -> Bool
isYesValue "t" = True
isYesValue "true" = True
isYesValue "yes" = True
isYesValue _ = False

-- if document contains a Div with id="refs", insert
-- references as its contents.  Otherwise, insert references
-- at the end of the document in a Div with id="refs" or
-- id=".*__refs" (where .* stands for any string -- this is
-- because --file-scope will add such a prefix based on the filename,
-- see #11072.)
-- | Insert refs using Pandoc's citation semantics.
insertRefs :: [(Text,Text)] -> [Text] -> [Block] -> Pandoc -> Pandoc
insertRefs _ _ [] d = d
insertRefs refkvs refclasses refs (Pandoc meta bs) =
  case lookupMeta "suppress-bibliography" meta of
    Just x | truish x -> Pandoc meta bs
    _ -> case runState (walkM go (Pandoc meta bs)) False of
               (d', True) -> d'
               (Pandoc meta' bs', False)
                 -> Pandoc meta' $
                    case refTitle meta of
                      Nothing ->
                        case reverse bs' of
                          Header lev (id',classes,kvs) ys : xs ->
                            reverse xs ++
                            [Header lev (id',addUnNumbered classes,kvs) ys,
                             Div ("refs",refclasses,refkvs) refs]
                          _ -> bs' ++ [refDiv]
                      Just ils -> bs' ++
                        [Header 1 ("bibliography", ["unnumbered"], []) ils,
                         refDiv]
  where
   refDiv = Div ("refs", refclasses, refkvs) refs
   addUnNumbered cs = "unnumbered" : [c | c <- cs, c /= "unnumbered"]
   go :: Block -> State Bool Block
   go (Div (ident,cs,kvs) xs)
     | ident == "refs" || "__refs" `T.isSuffixOf` ident = do
       put True
       -- refHeader isn't used if you have an explicit references div
       let cs' = nubOrd $ cs ++ refclasses
       let kvs' = nubOrd $ kvs ++ refkvs
       return $ Div (ident,cs',kvs') (xs ++ refs)
   go x = return x

-- | Ref title using Pandoc's citation semantics.
refTitle :: Meta -> Maybe [Inline]
refTitle meta =
  case lookupMeta "reference-section-title" meta of
    Just (MetaString s)           -> Just [Str s]
    Just (MetaInlines ils)        -> Just ils
    Just (MetaBlocks [Plain ils]) -> Just ils
    Just (MetaBlocks [Para ils])  -> Just ils
    _                             -> Nothing

-- | Extract text using Pandoc's citation semantics.
extractText :: Val Inlines -> Text
extractText (TextVal x)  = x
extractText (FancyVal x) = toText x
extractText (NumVal n)   = T.pack (show n)
extractText _            = mempty

-- Here we take the Spans with class csl-note that are left
-- after deNote has removed nested ones, and convert them
-- into real notes.
-- | Add note using Pandoc's citation semantics.
addNote :: Inline -> Inline
addNote (Span ("",["csl-note"],[]) ils) =
  Note [Para $
         B.toList . addTextCase Nothing CapitalizeFirst . B.fromList $ ils]
addNote x = x

-- Here we handle citation notes that occur inside footnotes
-- or other citation notes, in a note style.  We don't want
-- notes inside notes, so we convert these to parenthesized
-- or comma-separated citations.
-- | De note using Pandoc's citation semantics.
deNote :: Inline -> Inline
deNote (Note bs) =
  case bs of
    [Para (cit@(Cite (c:_) _) : ils)]
       | citationMode c /= AuthorInText ->
         -- if citation is first in note, no need to parenthesize.
         Note [Para (walk removeNotes $ cit : walk addParens ils)]
    _ -> Note (walk removeNotes . walk addParens $ bs)

 where
  addParens [] = []
  addParens (Cite (c:cs) ils : zs)
    | citationMode c == AuthorInText
      = Cite (c:cs) (addCommas (needsPeriod zs) ils) :
        addParens zs
    | otherwise
      = Cite (c:cs) (dropWhile (== Space) (concatMap noteInParens ils))
         : addParens zs
  addParens (x:xs) = x : addParens xs

  removeNotes (Span ("",["csl-note"],[]) ils) = Span ("",[],[]) ils
  removeNotes x = x

  needsPeriod [] = True
  needsPeriod (Str t:_) = case T.uncons t of
                            Nothing    -> False
                            Just (c,_) -> isUpper c
  needsPeriod (Space:zs) = needsPeriod zs
  needsPeriod _ = False

  noteInParens (Span ("",["csl-note"],[]) ils)
       = Space : Str "(" :
         removeFinalPeriod ils ++ [Str ")"]
  noteInParens x = [x]

  -- We want to add a comma before a CSL note citation, but not
  -- before the author name, and not before the first citation
  -- if it doesn't begin with an author name.
  addCommas = addCommas' True -- boolean == "at beginning"

  addCommas' _ _ [] = []
  addCommas' atBeginning needsPer
    (Span ("",["csl-note"],[]) ils : rest)
      | not (null ils)
       = (if atBeginning then id else ([Str "," , Space] ++)) $
         (if needsPer then ils else removeFinalPeriod ils) ++
         addCommas' False needsPer rest
  addCommas' _ needsPer (il : rest) = il : addCommas' False needsPer rest

deNote x = x

-- Note: we can't use dropTextWhileEnd indiscriminately,
-- because this would remove the final period on abbreviations like Ibid.
-- But it turns out that when the note citation ends with Ibid.
-- (or Ed. etc.), the last inline will be Str "" as a result of
-- the punctuation-fixing mechanism that removes the double '.'.
-- | Remove final period using Pandoc's citation semantics.
removeFinalPeriod :: [Inline] -> [Inline]
removeFinalPeriod ils =
  case lastMay ils of
    Just (Span attr ils')
      -> initSafe ils ++ [Span attr (removeFinalPeriod ils')]
    Just (Emph ils')
      -> initSafe ils ++ [Emph (removeFinalPeriod ils')]
    Just (Strong ils')
      -> initSafe ils ++ [Strong (removeFinalPeriod ils')]
    Just (SmallCaps ils')
      -> initSafe ils ++ [SmallCaps (removeFinalPeriod ils')]
    Just (Str t)
      | T.takeEnd 1 t == "." -> initSafe ils ++ [Str (T.dropEnd 1 t)]
      | isRightQuote (T.takeEnd 1 t)
        -> removeFinalPeriod
             (initSafe ils ++ [Str tInit | not (T.null tInit)]) ++ [Str tEnd]
             where
               tEnd  = T.takeEnd 1 t
               tInit = T.dropEnd 1 t
    _ -> ils
 where
  isRightQuote "\8221" = True
  isRightQuote "\8217" = True
  isRightQuote "\187"  = True
  isRightQuote _       = False

-- | Bcp47lang to ietf using Pandoc's citation semantics.
bcp47LangToIETF :: PandocMonad m => Text -> m (Maybe Lang)
bcp47LangToIETF bcplang =
  case parseLang bcplang of
    Left _ -> do
      report $ InvalidLang bcplang
      return Nothing
    Right lang -> return $ Just lang

-- | Interpret path/language metadata exactly as Pandoc 3.11 does.
-- | Meta value to text using Pandoc's citation semantics.
metaValueToText :: MetaValue -> Maybe Text
metaValueToText (MetaString t) = Just t
metaValueToText (MetaInlines ils) = Just $ stringify ils
metaValueToText (MetaBlocks bls) = Just $ stringify $ blocksToInlines' bls
metaValueToText (MetaList xs) = T.unwords <$> mapM metaValueToText xs
metaValueToText _ = Nothing
