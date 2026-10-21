; Real-MIR shape corpus for the AArch64 G_CONCAT_VECTORS policy slice.
; Each function is a two-source concat (k=2); the MIR companion file covers
; k=4 and k=8.  Run through:
;   llc -mtriple=aarch64-unknown-linux-gnu -global-isel -global-isel-abort=0 \
;       -stop-after=legalizer -debug-only=legalizer
target triple = "aarch64-unknown-linux-gnu"

define <4 x i8> @cat_v4s8(<2 x i8> %a, <2 x i8> %b) {
  %r = shufflevector <2 x i8> %a, <2 x i8> %b, <4 x i32> <i32 0, i32 1, i32 2, i32 3>
  ret <4 x i8> %r
}

define <8 x i8> @cat_v8s8(<4 x i8> %a, <4 x i8> %b) {
  %r = shufflevector <4 x i8> %a, <4 x i8> %b, <8 x i32> <i32 0, i32 1, i32 2, i32 3, i32 4, i32 5, i32 6, i32 7>
  ret <8 x i8> %r
}

define <4 x i16> @cat_v4s16(<2 x i16> %a, <2 x i16> %b) {
  %r = shufflevector <2 x i16> %a, <2 x i16> %b, <4 x i32> <i32 0, i32 1, i32 2, i32 3>
  ret <4 x i16> %r
}

define <8 x i16> @cat_v8s16(<4 x i16> %a, <4 x i16> %b) {
  %r = shufflevector <4 x i16> %a, <4 x i16> %b, <8 x i32> <i32 0, i32 1, i32 2, i32 3, i32 4, i32 5, i32 6, i32 7>
  ret <8 x i16> %r
}

define <4 x i32> @cat_v4s32(<2 x i32> %a, <2 x i32> %b) {
  %r = shufflevector <2 x i32> %a, <2 x i32> %b, <4 x i32> <i32 0, i32 1, i32 2, i32 3>
  ret <4 x i32> %r
}

define <16 x i1> @cat_v16s1(<8 x i1> %a, <8 x i1> %b) {
  %r = shufflevector <8 x i1> %a, <8 x i1> %b, <16 x i32> <i32 0, i32 1, i32 2, i32 3, i32 4, i32 5, i32 6, i32 7, i32 8, i32 9, i32 10, i32 11, i32 12, i32 13, i32 14, i32 15>
  ret <16 x i1> %r
}

define <16 x i8> @cat_v16s8(<8 x i8> %a, <8 x i8> %b) {
  %r = shufflevector <8 x i8> %a, <8 x i8> %b, <16 x i32> <i32 0, i32 1, i32 2, i32 3, i32 4, i32 5, i32 6, i32 7, i32 8, i32 9, i32 10, i32 11, i32 12, i32 13, i32 14, i32 15>
  ret <16 x i8> %r
}

define <32 x i8> @cat_v32s8(<16 x i8> %a, <16 x i8> %b) {
  %r = shufflevector <16 x i8> %a, <16 x i8> %b, <32 x i32> <i32 0, i32 1, i32 2, i32 3, i32 4, i32 5, i32 6, i32 7, i32 8, i32 9, i32 10, i32 11, i32 12, i32 13, i32 14, i32 15, i32 16, i32 17, i32 18, i32 19, i32 20, i32 21, i32 22, i32 23, i32 24, i32 25, i32 26, i32 27, i32 28, i32 29, i32 30, i32 31>
  ret <32 x i8> %r
}
define <4 x float> @cat_v4f32(<2 x float> %a, <2 x float> %b) {
  %r = shufflevector <2 x float> %a, <2 x float> %b, <4 x i32> <i32 0, i32 1, i32 2, i32 3>
  ret <4 x float> %r
}
