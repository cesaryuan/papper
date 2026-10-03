{-# LANGUAGE OverloadedStrings   #-}
{-# LANGUAGE MonoLocalBinds      #-}
{-# LANGUAGE RankNTypes          #-}
{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE TupleSections       #-}
{- |
   Module      : Text.Pandoc.Readers
   Copyright   : Copyright (C) 2006-2024 John MacFarlane
   License     : GNU GPL, version 2 or above

   Maintainer  : John MacFarlane <jgm@berkeley.edu>
   Stability   : alpha
   Portability : portable

This helper module exports the readers.

Note:  all of the readers assume that the input text has @'\n'@
line endings.  So if you get your input text from a web form,
you should remove @'\r'@ characters using @filter (/='\r')@.

-}

module Text.Pandoc.Readers
  (
    -- * Readers: converting /to/ Pandoc format
    Reader (..)
  , readers
  , readAsciiDoc
  , readDocx
  , readPptx
  , readXlsx
  , readODT
  , readMarkdown
  , readCommonMark
  , readCreole
  , readDokuWiki
  , readMediaWiki
  , readVimwiki
  , readRST
  , readOrg
  , readLaTeX
  , readHtml
  , readJATS
  , readJira
  , readTextile
  , readDocBook
  , readOPML
  , readHaddock
  , readNative
  , readJSON
  , readTWiki
  , readTikiWiki
  , readTxt2Tags
  , readEPUB
  , readMuse
  , readMan
  , readMdoc
  , readFB2
  , readIpynb
  , readCSV
  , readTSV
  , readCslJson
  , readBibTeX
  , readBibLaTeX
  , readEndNoteXML
  , readRIS
  , readRTF
  , readTypst
  , readDjot
  , readPod
  , readXML
  -- * Miscellaneous
  , getReader
  , getDefaultExtensions
  ) where

import Control.Monad.Except (throwError)
import Data.Aeson
import qualified Data.ByteString.Lazy as BL
import Data.Text (Text)
import qualified Data.Text as T
import Text.Pandoc.Class
import Text.Pandoc.Definition
import Text.Pandoc.Error
import Text.Pandoc.Extensions
import qualified Text.Pandoc.Format as Format
import Text.Pandoc.Options
import Text.Pandoc.Readers.AsciiDoc
import Text.Pandoc.Readers.CommonMark
import Text.Pandoc.Readers.Markdown
import Text.Pandoc.Readers.Creole
import Text.Pandoc.Readers.DocBook
import Text.Pandoc.Readers.Docx
import Text.Pandoc.Readers.Pptx
import Text.Pandoc.Readers.Xlsx
import Text.Pandoc.Readers.DokuWiki
import Text.Pandoc.Readers.EPUB
import Text.Pandoc.Readers.FB2
import Text.Pandoc.Readers.Ipynb
import Text.Pandoc.Readers.Haddock
import Text.Pandoc.Readers.HTML (readHtml)
import Text.Pandoc.Readers.JATS (readJATS)
import Text.Pandoc.Readers.Jira (readJira)
import Text.Pandoc.Readers.LaTeX
import Text.Pandoc.Readers.MediaWiki
import Text.Pandoc.Readers.Muse
import Text.Pandoc.Readers.Native
import Text.Pandoc.Readers.ODT
import Text.Pandoc.Readers.OPML
import Text.Pandoc.Readers.Org
import Text.Pandoc.Readers.Pod
import Text.Pandoc.Readers.RST
import Text.Pandoc.Readers.Textile
import Text.Pandoc.Readers.TikiWiki
import Text.Pandoc.Readers.TWiki
import Text.Pandoc.Readers.Txt2Tags
import Text.Pandoc.Readers.Vimwiki
import Text.Pandoc.Readers.Man
import Text.Pandoc.Readers.Mdoc
import Text.Pandoc.Readers.CSV
import Text.Pandoc.Readers.CslJson
import Text.Pandoc.Readers.BibTeX
import Text.Pandoc.Readers.EndNote
import Text.Pandoc.Readers.RIS
import Text.Pandoc.Readers.RTF
import Text.Pandoc.Readers.Typst
import Text.Pandoc.Readers.Djot
import Text.Pandoc.Readers.XML
import qualified Text.Pandoc.UTF8 as UTF8
import Text.Pandoc.Sources (ToSources(..), sourcesToText)

data Reader m = TextReader (forall a . ToSources a =>
                                ReaderOptions -> a -> m Pandoc)
              | ByteStringReader (ReaderOptions -> BL.ByteString -> m Pandoc)

-- | Papper's supported readers; bibliography formats remain available to citeproc.
-- Keep upstream exports for library/Lua compatibility, but remove unused registry
-- roots so the executable linker can discard unrelated conversion implementations.
readers :: PandocMonad m => [(Text, Reader m)]
readers = [("native"       , TextReader readNative)
          ,("json"         , TextReader readJSON)
          ,("markdown"     , TextReader readMarkdown)
          ,("markdown_strict" , TextReader readMarkdown)
          ,("markdown_phpextra" , TextReader readMarkdown)
          ,("markdown_github" , TextReader readMarkdown)
          ,("markdown_mmd",  TextReader readMarkdown)
          ,("commonmark"   , TextReader readCommonMark)
          ,("commonmark_x" , TextReader readCommonMark)
          ,("gfm"          , TextReader readCommonMark)
          ,("html"         , TextReader readHtml)
          ,("latex"        , TextReader readLaTeX)
          ,("docx"         , ByteStringReader readDocx)
          ,("csljson"      , TextReader readCslJson)
          ,("bibtex"       , TextReader readBibTeX)
          ,("biblatex"     , TextReader readBibLaTeX)
          ,("endnotexml"   , TextReader readEndNoteXML)
          ,("ris"          , TextReader readRIS)
          ]

-- | Retrieve reader, extensions based on format spec (format+extensions).
getReader :: PandocMonad m => Format.FlavoredFormat -> m (Reader m, Extensions)
getReader flvrd = do
  let readerName = Format.formatName flvrd
  case lookup readerName readers of
    Nothing  -> throwError $ PandocUnknownReaderError readerName
    Just  r  -> (r,) <$>
      Format.applyExtensionsDiff (Format.getExtensionsConfig readerName) flvrd

-- | Read pandoc document from JSON format.
readJSON :: (PandocMonad m, ToSources a)
         => ReaderOptions
         -> a
         -> m Pandoc
readJSON _ s =
  case eitherDecode' . BL.fromStrict . UTF8.fromText
                     . sourcesToText . toSources $ s of
       Right doc -> return doc
       Left e    -> throwError $ PandocParseError ("JSON parse error: "
                                                   <> T.pack e)
