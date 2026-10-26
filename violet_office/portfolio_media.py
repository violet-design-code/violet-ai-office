"""Bounded photo validation. No arbitrary paths, EXIF imports or external fetch."""
import struct
import zlib


def validate_photo(blob):
    if not isinstance(blob,bytes) or not 0<len(blob)<=2*1024*1024: raise ValueError('Photo must be <=2MiB')
    if blob.startswith(b'\x89PNG\r\n\x1a\n'):
        pos=8; header=False; data=False; end=False; chunks=0
        while pos+12<=len(blob):
            chunks+=1
            if chunks>1000: raise ValueError('Too many image chunks')
            size=struct.unpack('>I',blob[pos:pos+4])[0]; tag=blob[pos+4:pos+8]
            if size>2*1024*1024 or pos+12+size>len(blob): raise ValueError('Invalid PNG chunk')
            payload=blob[pos+8:pos+8+size]
            if zlib.crc32(tag+payload)&0xffffffff!=struct.unpack('>I',blob[pos+8+size:pos+12+size])[0]: raise ValueError('Invalid PNG CRC')
            if tag not in {b'IHDR',b'IDAT',b'IEND',b'PLTE',b'tRNS',b'sRGB',b'gAMA',b'cHRM',b'pHYs',b'sBIT',b'bKGD'}: raise ValueError('Unsupported or private image metadata')
            if tag in (b'tEXt',b'iTXt',b'zTXt',b'eXIf'): raise ValueError('Remove photo metadata before upload')
            if not header:
                if tag!=b'IHDR' or size!=13: raise ValueError('PNG header required')
                w,h=struct.unpack('>II',payload[:8])
                if not 0<w*h<=20000000: raise ValueError('Photo dimensions too large')
                header=True
            if tag==b'IDAT': data=True
            pos+=12+size
            if tag==b'IEND':
                if size or pos!=len(blob): raise ValueError('No trailing photo content')
                end=True; break
        if not(header and data and end): raise ValueError('Complete PNG required')
        return 'image/png'
    if blob.startswith(b'\xff\xd8') and blob.endswith(b'\xff\xd9'):
        if any(marker in blob for marker in (b'\xff\xe1',b'\xff\xed',b'\xff\xfe')): raise ValueError('Remove all photo metadata before upload')
        pos=2; frame=False; scan=False
        while pos<len(blob)-2:
            if blob[pos]!=255: raise ValueError('JPEG segment required')
            while pos<len(blob) and blob[pos]==255: pos+=1
            marker=blob[pos]; pos+=1
            if marker in (0xe1,0xed,0xfe): raise ValueError('Remove photo metadata before upload')
            if marker==0xda: scan=True; break
            if pos+2>len(blob): raise ValueError('Invalid JPEG')
            length=int.from_bytes(blob[pos:pos+2],'big')
            if length<2 or pos+length>len(blob): raise ValueError('Invalid JPEG length')
            if marker in (0xc0,0xc2):
                if length<8: raise ValueError('JPEG frame required')
                h=int.from_bytes(blob[pos+3:pos+5],'big'); w=int.from_bytes(blob[pos+5:pos+7],'big')
                if not 0<w*h<=20000000: raise ValueError('Photo dimensions too large')
                frame=True
            pos+=length
        if not(frame and scan): raise ValueError('JPEG frame and scan required')
        return 'image/jpeg'
    raise ValueError('PNG or JPEG required; HTML/SVG/remote URLs are not accepted')
