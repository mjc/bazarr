# coding=utf-8
from typing import TypeVar

CodecValue = TypeVar("CodecValue")


def RadarrFormatAudioCodec(audioFormat: CodecValue, audioCodecID: str | None, audioProfile: str | None,
                           audioAdditionalFeatures: str | None) -> str | CodecValue:
    if type(audioFormat) is not str:
        return audioFormat
    return _normalize_audio_codec(audioFormat, audioCodecID, audioProfile, audioAdditionalFeatures)


def _normalize_audio_codec(audioFormat: str, audioCodecID: str | None, audioProfile: str | None,
                           audioAdditionalFeatures: str | None) -> str:
    if audioFormat == "AC-3":
        return "AC3"
    if audioFormat == "E-AC-3":
        return "EAC3"
    if audioFormat == "AAC" and audioCodecID == "A_AAC/MPEG4/LC/SBR":
        return "HE-AAC"
    if audioFormat.strip() == "mp3":
        return "MP3"
    if audioFormat == "MPEG Audio":
        if audioCodecID in ("55", "A_MPEG/L3") or audioProfile == "Layer 3":
            return "MP3"
        if audioCodecID == "A_MPEG/L2" or audioProfile == "Layer 2":
            return "MP2"
    if audioFormat == "MLP FBA":
        return "TrueHD Atmos" if audioAdditionalFeatures == "16-ch" else "TrueHD"
    return audioFormat


def RadarrFormatVideoCodec(videoFormat: CodecValue, videoCodecID: str | None,
                           videoCodecLibrary: str | None) -> str | CodecValue:
    if type(videoFormat) is not str:
        return videoFormat
    return _normalize_video_codec(videoFormat, videoCodecID, videoCodecLibrary)


def _normalize_video_codec(videoFormat: str, videoCodecID: str | None, videoCodecLibrary: str | None) -> str:
    if videoFormat in ("x264", "AVC", "V.MPEG4/ISO/AVC"):
        return "h264"
    if videoFormat in ("HEVC", "V_MPEGH/ISO/HEVC") and videoCodecLibrary and videoCodecLibrary.startswith("x265"):
        return "h265"
    if videoFormat == "MPEG Video" and videoCodecID:
        return "Mpeg2" if videoCodecID in ("2", "V_MPEG2") else "Mpeg"
    if videoFormat == "MPEG-1 Video":
        return "Mpeg"
    if videoFormat == "MPEG-2 Video":
        return "Mpeg2"
    if videoFormat == "MPEG-4 Visual" and videoCodecLibrary and videoCodecID:
        if videoCodecID.endswith("XVID") or videoCodecLibrary.startswith("XviD"):
            return "XviD"
        if videoCodecID.endswith(("DIV3", "DIVX", "DX50")) or videoCodecLibrary.startswith("DivX"):
            return "DivX"
    if videoFormat == "VC-1":
        return "VC1"
    if videoFormat == "WMV2":
        return "WMV"
    if videoFormat in ("DivX", "div3"):
        return "DivX"
    return videoFormat
