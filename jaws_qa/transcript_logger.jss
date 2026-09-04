; ===================================================================
; transcript_logger.jss   —   JAWS 2021 speech logger (Spike B artifact)
;
; Logs everything JAWS speaks to a timestamped file so a course run can
; be reviewed and searched as text, and later merged (by timestamp) with
; the companion's screen-change markers.
;
; THIS IS A STARTING POINT, NOT VERIFIED COMPLETE. The spike is to prove
; the two points marked << VERIFY below. If either fails, fall back to
; audio + speech-to-text (Option C in JAWS_TRANSCRIPT_DESIGN.md).
;
;   << VERIFY 1 (COVERAGE): does hooking SayString capture the COURSE's
;      speech, or does JAWS read slide content through a path this never
;      sees? Compare the log to what you actually hear on a few slides.
;   << VERIFY 2 (FILE API): confirm FileOpenWrite / FileWriteLine /
;      FileClose names and the append flag against the JAWS 2021 API,
;      and that GetTickCount exists (else use the 2021 equivalent).
; ===================================================================

Include "hjconst.jsh"

Const
    kLogFile = "C:\jaws_qa\transcript.log"     ; must match the companion's clock source

; ---- write one line: "<ms> | <text>" ------------------------------
Void Function TLog (String text)
    Var
        Int h
    Let h = FileOpenWrite (kLogFile, 1)         ; 1 = append   << VERIFY 2
    If h > 0 Then
        FileWriteLine (h, IntToString(GetTickCount()) + " | " + text)
        FileClose (h)
    EndIf
EndFunction

; ---- hook: JAWS calls SayString to speak most strings -------------
Void Function SayString (String buffer, Int mode)
    TLog (buffer)
    SayString (buffer, mode)                    ; pass through to the built-in
                                                ; << VERIFY: this must call the
                                                ; built-in, NOT recurse into this
                                                ; function. If JAWS goes silent or
                                                ; hangs, that assumption is wrong.
EndFunction
