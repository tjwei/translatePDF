#!/usr/bin/env python2
import zlib
from pdfrw import PdfReader, PdfWriter, PdfTokens, PdfString, PdfArray
import fontTools.ttLib
import PyOpenCC as opencc
import chardet
import cStringIO as StringIO
import argparse
import os.path
import sys

# monkey patch, fix bugs of pdfrw
import re
PdfString.unescape_dict = {'\\b':'\b', '\\f':'\f', '\\n':'\n',
                     '\\r':'\r', '\\t':'\t',
                     '\\\r\n': '', '\\\r':'', '\\\n':'',
                     '\\\\':'\\', '\\':'', '\\(': '(', '\\)':')'
                    }
PdfString.unescape_pattern = r'(\\\)|\\\(|\\b|\\f|\\n|\\r|\\t|\\\r\n|\\\r|\\\n|\\[0-9]+|\\\\)'
PdfString.unescape_func = re.compile(PdfString.unescape_pattern).split

#Utility for display Chinese font name
def autoDecode(s):    
    enc=chardet.detect(s)["encoding"]
    if enc:
        return s.decode(chardet.detect(s)["encoding"], "replace")
    else:
        return s

# Utility functions for reading/writing pdf stream
def readStream(obj):
    try:
        if obj.has_key("/Filter"):
            return zlib.decompress(obj.stream)
        else:
            return obj.stream
    except:
        print sys.exc_info()
        print "readStream: The object does not have a stream?", obj
        return ""
        
def writeStream(obj, buf):
    try:
        if obj.has_key("/Filter"):
            obj.stream=zlib.compress(buf)
        else:
            obj.stream=buf
    except:
        print sys.exc_info()
        print "writeStream: The object does not have a stream?", obj
        pass

#Utility function for getting the Decode Dict from a font
def getFontDecodeDict(font):
    """
                Only works for font with ToUnicode and DesendantFont...FontFile2
                This function is a quick ugly function, may not work.
    """    
    # Attempt to use the cmap from FontFile2
    ttfbuf=readStream(font.DescendantFonts[0].FontDescriptor.FontFile2)
    tmpttf=fontTools.ttLib.TTFont(StringIO.StringIO(ttfbuf))
    cmap=None
    if tmpttf.has_key('cmap'):
        if tmpttf['cmap'].getcmap(3,1):
            cmap=tmpttf['cmap'].getcmap(3,1).cmap
        elif tmpttf['cmap'].getcmap(3,10): 
            cmap10=tmpttf['cmap'].getcmap(3,10).cmap
            cmap={k&0xffff:v for k,v in cmap10.iteritems()}
    if cmap:
        return {tmpttf.getGlyphID(n): unichr(u) for u, n in cmap.iteritems()}
    # No cmap, use ToUnicode
    print "cmap not found, using /ToUnicode for", autoDecode(font.BaseFont)
    rtn={}    
    tokens=PdfTokens(readStream(font.ToUnicode))
    doRange=False
    working_list=nums=[]    
    for tok in tokens:        
        if tok=='beginbfrange':
            doRange=True            
        elif tok=='endbfrange':
            doRange=False            
        elif tok[:]=='[':
            working_list=[]            
        elif tok[:]==']':
            nums.append(working_array)
            working_list=nums
        elif tok[0]=='<' and tok[-1]=='>':                            
            working_list.append(int(tok[1:-1],16))        
        if doRange and len(nums)>=3:
            start, end, target=nums[0],nums[1], nums[2]
            if isinstance(target, list):
                for i in range(start, end+1):
                    rtn[i]=unichr(target[i-start])
            else:
                for i in range(start, end+1):
                    rtn[i]=unichr(target+i-start)
            working_list=nums=[]
        elif not doRange and len(nums)>=2:            
            rtn[nums[0]]=unichr(nums[1])
            working_list=nums=[]        
    return rtn

#utility function: permanent id
def _id(obj, objlist=[]):
    for i,x in enumerate(objlist):
        if x is obj:
            return i
    objlist.append(obj)
    return len(objlist)-1

def transPdfString(v, translator)        :
    if isinstance(v, PdfString):                    
        if v[0]=="(":
            s0=v.decode()
            if s0.startswith("\xfe\xff"): #chardet.detect(s0)["encoding"]=="UTF-16BE":
                s1=translator(s0.decode("utf-16be", "ignore"))  
                s2=PdfString.encode(s1.encode("utf-16be"))                        
                return PdfString(s2)
    return None
TTF_FILE="/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"

class TranslatedPdf(object):
    def __init__(self, pdfFile, translator, ttfFile=TTF_FILE):
        """            
                pdfFile is the file name or file object of the pdf file we want to translate
                translator is a unicode to unicode function
                ttfFile is the default ttf font file name used in translated pdf
        """
        try:
            self.pdf=PdfReader(pdfFile, decompress=False)
        except:
            print "Using pdftk to uncompress and decrypt"
            from subprocess import Popen, PIPE            
            cmd = ['pdftk', pdfFile, 'output', '-',  'uncompress']
            proc = Popen(cmd,stdout=PIPE)
            cmdout,cmderr = proc.communicate()
            if cmderr:
                print "Unable to open", pdfFile
                sys.exit(1)
            self.pdf=PdfReader(StringIO.StringIO(cmdout), decompress=False)
        self.decodeDicts={}
        self.font_list=[]
        self.dttf=fontTools.ttLib.TTFont(TTF_FILE) # default ttf
        if self.dttf['cmap'].getcmap(3,1) is None:        
            cmap10=self.dttf['cmap'].getcmap(3,10).cmap
            cmap={k&0xffff:v for k,v in cmap10.iteritems()}
        else:
            cmap=self.dttf['cmap'].getcmap(3,1).cmap
        self.ttf_cmap=cmap
        for n, page in enumerate(self.pdf.pages):
            print "Translating p", n , "\r",
            self._translatePage(page, translator)
        # translate fonts
        for font in self.font_list:            
            fontfile=font.DescendantFonts[0].FontDescriptor.FontFile2
            writeStream(fontfile, file(TTF_FILE,"rb").read())
            font.ToUnicode=None
        # translate info
        if self.pdf.has_key("/Info"):
            for k,v in self.pdf.Info.iteritems():
                tv=transPdfString(v, translator)
                if tv is not None:
                    self.pdf.Info[k]=tv                    
        # translate outlines        
        if self.pdf.Root.Outlines:
            array=[]
            array.append(self.pdf.Root.Outlines.First)
            while array:
                x=array.pop()
                if x.First:
                    array.append(x.First)
                if x.Next:
                    array.append(x.Next)                                
                tTitle=transPdfString(x.Title, translator)
                if tTitle is not None:
                    x.Title=tTitle                
                
    def _updatePageFontDecodeDicts(self, page):
            fonts=page.Resources.Font            
            for k, font in (fonts.iteritems() if fonts else []):                        
                font_id=_id(font)
                if font_id not in self.decodeDicts:                    
                    if font.has_key("/ToUnicode") and font.has_key("/DescendantFonts"):
                        print "Font translated:",k, autoDecode(font.BaseFont)
                        self.decodeDicts[font_id]=getFontDecodeDict(font)
                        self.font_list.append(font)
                    else:
                        print "Font not translated:", k, autoDecode(font.BaseFont)
                        self.decodeDicts[font_id]=None    
                        
    def saveAs(self, fname):
        opdf=PdfWriter()
        #print type(opdf.trailer), type(opdf.trailer.Info), type(opdf.trailer.Info.Author)
        opdf.addpages(self.pdf.pages)        
        opdf.trailer.Info=self.pdf.Info
        opdf.trailer.Root.Outlines=self.pdf.Root.Outlines
        opdf.write(fname)

    def _translatePage(self, page, translator):
        def handleText(encoded_text, decodeDict):
            if not decodeDict: 
                return encoded_text #unable to decode the text
            if encoded_text[0]!='<': # Unhandled case, never happend
                print encoded_text[:]
                return encoded_text
            b=encoded_text.decode()
            utext0=u""
            b0=b            
            while len(b):
                code = ord(b[0])*256+ord(b[1]) if len(b)>1 else ord(b[0])
                if decodeDict.has_key(code):
                    utext0 += decodeDict[code]
                else:
                    utext0 += "??"
                    print "\n??", hex(code), [hex(ord(x)) for x in str(b0)]                    
                b=b[2:]
            utext=translator(utext0)                
            gid_array=[]
            for x in utext:
                try:
                    name=self.ttf_cmap[ord(x)]
                    gid=self.dttf.getGlyphID(name)
                    gid_array.append(gid)
                except:
                    print "no gid%d"%ord(x), x                
            return "<"+"".join("%04X"%gid for gid in gid_array)+">"             
        self._updatePageFontDecodeDicts(page)
        output=""
        contents=page.Contents
        fonts=page.Resources.Font
        tokens=PdfTokens(readStream(contents))
        operands=[]
        decodeDict=None
        for tok in tokens:
            if str.isalpha(tok[0]) or tok[0] in ['"', "'"]:            
                if tok=='Tf':
                    font_name=operands[0]                    
                    decodeDict=self.decodeDicts[_id(fonts[font_name])]
                elif tok=="Tj":                    
                    operands[0]=handleText(operands[0], decodeDict)                    
                elif tok=="TJ":                
                    for n,t in enumerate(operands[1:]):
                        if t==']':
                            break
                        try:
                            tokNum=float(t)
                        except:
                            tokNum=None
                        if tokNum==None:
                            operands[n+1]=handleText(t, decodeDict)
                output += " ".join(operands+[tok]) + "\n"            
                operands=[]            
            else:
                operands.append(tok)    
        writeStream(contents, output)
        
def main():
    parser = argparse.ArgumentParser(
        description='Translate a PDF file from/to different variations of Chinese language',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--opencc-config', type=str, default="zhs2zhtw_vp.ini", 
                        help="opencc config")
    parser.add_argument('--default-ttf', type=str, default=TTF_FILE, 
                        help="default TTF font file name")
    parser.add_argument('--output-prefix', type=str, default="[translated]", 
                        help="""default output prefix. 
Ignored when output file name is given""")
    parser.add_argument("input", type=str, help="input pdf file")
    parser.add_argument("output", type=str, nargs="?", help="output pdf file name")
    args=args = parser.parse_args()
    with opencc.OpenCC(args.opencc_config) as cc:
        translator = lambda x: cc.convert(x.encode("utf8")).decode("utf8", "replace")                
        tpdf=TranslatedPdf(args.input, translator, args.default_ttf)
        if args.output is None:
            inputFileName=autoDecode(os.path.basename(args.input)) # to unicode
            args.output=autoDecode(args.output_prefix)+translator(inputFileName)    
    print "\nwriting", args.output
    tpdf.saveAs(args.output)
if __name__ == "__main__":
    main()
