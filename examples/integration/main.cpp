//===-- main.cpp - generated LegalizerInfo JSONL consumer -*- C++ -*-===//
//
// Reads one LegalityQuery per line, asks the generated LegalizerInfo for the
// exact LegalizeActionStep, and prints the observation as JSONL.  The only
// policy in this file is parsing and formatting; the rules come exclusively
// from the generated static library.
//
// Input:
//   {"id":0,"opcode":"G_ADD","types":["s32"],"mems":[],"imms":[]}
// Output:
//   {"id":0,"action":"Legal","type_idx":0,"new_type":null}
//
//===----------------------------------------------------------------------===//

#include "llvm/ADT/StringRef.h"
#include "llvm/CodeGen/GlobalISel/LegalizerInfo.h"
#include "llvm/CodeGen/TargetOpcodes.h"
#include "llvm/CodeGenTypes/LowLevelType.h"
#include "llvm/Support/Error.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/raw_ostream.h"

// The generated wiring includes the generated header and defines
// LEGALIZER_DSL_APPLY_RULES for this output stem.
#include "wiring.cpp.inc"

#include <cctype>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

using namespace llvm;

namespace {

std::string formatLLT(const LLT &Ty) {
  if (!Ty.isValid())
    return "";
  if (Ty.isVector())
    return "v" + std::to_string(Ty.getNumElements()) +
           formatLLT(Ty.getElementType());
  if (Ty.isPointer()) {
    std::string Result = "p" + std::to_string(Ty.getAddressSpace());
    if (Ty.getScalarSizeInBits() != 32)
      Result += ":" + std::to_string(Ty.getScalarSizeInBits());
    return Result;
  }
  if (Ty.isFloat()) {
    switch (Ty.getFpSemantics()) {
    case LLT::FpSemantics::S_IEEEhalf:
      return "f16";
    case LLT::FpSemantics::S_IEEEsingle:
      return "f32";
    case LLT::FpSemantics::S_IEEEdouble:
      return "f64";
    case LLT::FpSemantics::S_IEEEquad:
      return "f128";
    case LLT::FpSemantics::S_BFloat:
      return "bf16";
    case LLT::FpSemantics::S_x87DoubleExtended:
      return "x86_fp80";
    case LLT::FpSemantics::S_PPCDoubleDouble:
      return "ppcf128";
    default:
      return "f" + std::to_string(Ty.getScalarSizeInBits()) + "_unsupported";
    }
  }
  return "s" + std::to_string(Ty.getScalarSizeInBits());
}

Expected<unsigned> parseUnsigned(StringRef S) {
  unsigned Value = 0;
  if (S.empty())
    return createStringError("empty integer");
  for (char C : S) {
    if (!std::isdigit(static_cast<unsigned char>(C)))
      return createStringError("invalid integer: " + S);
    Value = Value * 10 + unsigned(C - '0');
  }
  return Value;
}

Expected<LLT> parseLLT(StringRef S) {
  if (S.empty())
    return createStringError("empty LLT");

  if (S.front() == 's') {
    S = S.drop_front();
    auto Bits = parseUnsigned(S);
    if (!Bits)
      return Bits.takeError();
    if (*Bits == 0)
      return createStringError("scalar size must be positive");
    return LLT::scalar(*Bits);
  }

  if (S.front() == 'p') {
    S = S.drop_front();
    std::string AddrText;
    while (!S.empty() && std::isdigit(static_cast<unsigned char>(S.front()))) {
      AddrText.push_back(S.front());
      S = S.drop_front();
    }
    auto AddrSpace = parseUnsigned(AddrText);
    if (!AddrSpace)
      return AddrSpace.takeError();
    unsigned Bits = 32;
    if (!S.empty()) {
      if (!S.consume_front(":"))
        return createStringError("junk after pointer address space: " + S);
      auto ParsedBits = parseUnsigned(S);
      if (!ParsedBits)
        return ParsedBits.takeError();
      Bits = *ParsedBits;
    }
    if (Bits == 0)
      return createStringError("pointer size must be positive");
    return LLT::pointer(*AddrSpace, Bits);
  }

  if (S == "bf16")
    return LLT::bfloat16();

  if (S.front() == 'f') {
    S = S.drop_front();
    auto Bits = parseUnsigned(S);
    if (!Bits)
      return Bits.takeError();
    switch (*Bits) {
    case 16:
      return LLT::float16();
    case 32:
      return LLT::float32();
    case 64:
      return LLT::float64();
    case 128:
      return LLT::float128();
    default:
      return createStringError("no IEEE float type with this bit width");
    }
  }

  if (S.front() == 'v') {
    S = S.drop_front();
    std::string LanesText;
    while (!S.empty() && std::isdigit(static_cast<unsigned char>(S.front()))) {
      LanesText.push_back(S.front());
      S = S.drop_front();
    }
    auto Lanes = parseUnsigned(LanesText);
    if (!Lanes)
      return Lanes.takeError();
    if (*Lanes < 2)
      return createStringError(
          "vector lane count must be at least 2: LLVM cannot represent v1");
    auto Elem = parseLLT(S);
    if (!Elem)
      return Elem.takeError();
    if (Elem->isVector())
      return createStringError("nested vectors are not an LLT");
    return LLT::fixed_vector(*Lanes, *Elem);
  }

  return createStringError("bad LLT spelling: " + S);
}

Expected<unsigned> lookupOpcode(StringRef Name) {
  static const std::unordered_map<std::string, unsigned> Opcodes = [] {
    std::unordered_map<std::string, unsigned> Map;
#undef HANDLE_TARGET_OPCODE
#undef HANDLE_TARGET_OPCODE_MARKER
#define HANDLE_TARGET_OPCODE(OPC) Map.emplace(#OPC, TargetOpcode::OPC);
#define HANDLE_TARGET_OPCODE_MARKER(IDENT, OPC)
#include "llvm/Support/TargetOpcodes.def"
#undef HANDLE_TARGET_OPCODE
#undef HANDLE_TARGET_OPCODE_MARKER
    return Map;
  }();

  auto It = Opcodes.find(Name.str());
  if (It == Opcodes.end())
    return createStringError("unknown target-independent opcode: " + Name);
  return It->second;
}

Expected<int64_t> requiredInteger(const json::Object &Object, StringRef Key) {
  auto Value = Object.getInteger(Key);
  if (!Value)
    return createStringError("missing or non-integer field: " + Key);
  return *Value;
}

Expected<StringRef> requiredString(const json::Object &Object, StringRef Key) {
  auto Value = Object.getString(Key);
  if (!Value)
    return createStringError("missing or non-string field: " + Key);
  return *Value;
}

struct QueryRecord {
  int64_t Id = 0;
  unsigned Opcode = 0;
  std::vector<LLT> Types;
  std::vector<LegalityQuery::MemDesc> Mems;
  std::vector<int64_t> Imms;
};

Expected<QueryRecord> parseRecord(StringRef Line) {
  auto Parsed = json::parse(Line);
  if (!Parsed)
    return Parsed.takeError();
  const json::Object *Object = Parsed->getAsObject();
  if (!Object)
    return createStringError("query must be a JSON object");

  QueryRecord Record;
  auto Id = requiredInteger(*Object, "id");
  if (!Id)
    return Id.takeError();
  Record.Id = *Id;

  auto OpcodeName = requiredString(*Object, "opcode");
  if (!OpcodeName)
    return OpcodeName.takeError();
  auto Opcode = lookupOpcode(*OpcodeName);
  if (!Opcode)
    return Opcode.takeError();
  Record.Opcode = *Opcode;

  const json::Array *Types = Object->getArray("types");
  if (!Types)
    return createStringError("missing or non-array field: types");
  for (const json::Value &Value : *Types) {
    auto Text = Value.getAsString();
    if (!Text)
      return createStringError("types[] must contain strings");
    auto Ty = parseLLT(*Text);
    if (!Ty)
      return Ty.takeError();
    Record.Types.push_back(*Ty);
  }

  const json::Array *Mems = Object->getArray("mems");
  if (!Mems)
    return createStringError("missing or non-array field: mems");
  for (const json::Value &Value : *Mems) {
    const json::Object *Mem = Value.getAsObject();
    if (!Mem)
      return createStringError("mems[] must contain objects");
    auto MemTypeText = requiredString(*Mem, "type");
    if (!MemTypeText)
      return MemTypeText.takeError();
    auto MemType = parseLLT(*MemTypeText);
    if (!MemType)
      return MemType.takeError();
    auto Align = requiredInteger(*Mem, "align");
    if (!Align)
      return Align.takeError();
    if (*Align <= 0)
      return createStringError("mem align must be positive");
    Record.Mems.emplace_back(*MemType, uint64_t(*Align), AtomicOrdering::NotAtomic,
                             AtomicOrdering::NotAtomic);
  }

  const json::Array *Imms = Object->getArray("imms");
  if (!Imms)
    return createStringError("missing or non-array field: imms");
  for (const json::Value &Value : *Imms) {
    auto Imm = Value.getAsInteger();
    if (!Imm)
      return createStringError("imms[] must contain integers");
    Record.Imms.push_back(*Imm);
  }

  return Record;
}

std::string actionName(LegalizeAction Action) {
  std::string Name;
  raw_string_ostream OS(Name);
  OS << Action;
  OS.flush();
  return Name;
}

void printJsonString(raw_ostream &OS, StringRef S) {
  OS << '"';
  for (char C : S) {
    switch (C) {
    case '"':
      OS << "\\\"";
      break;
    case '\\':
      OS << "\\\\";
      break;
    case '\n':
      OS << "\\n";
      break;
    case '\r':
      OS << "\\r";
      break;
    case '\t':
      OS << "\\t";
      break;
    default:
      OS << C;
      break;
    }
  }
  OS << '"';
}

void printResult(raw_ostream &OS, int64_t Id, const LegalizeActionStep &Step) {
  OS << "{\"id\":" << Id << ",\"action\":";
  printJsonString(OS, actionName(Step.Action));
  OS << ",\"type_idx\":" << Step.TypeIdx << ",\"new_type\":";
  if (Step.NewType.isValid())
    printJsonString(OS, formatLLT(Step.NewType));
  else
    OS << "null";
  OS << "}\n";
}

void printError(raw_ostream &OS, int64_t Id, StringRef Message) {
  OS << "{\"id\":" << Id << ",\"error\":";
  printJsonString(OS, Message);
  OS << "}\n";
}

LegalizerInfo &getGeneratedLI() {
  static LegalizerInfo LI;
  static const bool Initialized = [] {
#ifdef LDSL_USE_EXTENDED_LLT
    // AArch64's TargetMachine enables extended LLTs.  The generated consumer
    // has no TargetMachine, so it must declare the same configuration.
    LLT::setUseExtended(true);
#endif
    LEGALIZER_DSL_APPLY_RULES(LI);
    return true;
  }();
  (void)Initialized;
  return LI;
}

} // namespace

int main(int argc, char **argv) {
  std::string InputPath;
  for (int I = 1; I < argc; ++I) {
    StringRef Arg(argv[I]);
    if (Arg == "--input") {
      if (I + 1 >= argc) {
        errs() << "error: --input requires a path\n";
        return 2;
      }
      InputPath = argv[++I];
    } else if (Arg == "--help" || Arg == "-h") {
      outs() << "usage: legalizer-dsl-consumer [--input FILE]\n";
      return 0;
    } else {
      errs() << "error: unknown argument: " << Arg << "\n";
      return 2;
    }
  }

  LegalizerInfo &LI = getGeneratedLI();

  std::ifstream InputFile;
  std::istream *Input = &std::cin;
  if (!InputPath.empty()) {
    InputFile.open(InputPath, std::ios::binary);
    if (!InputFile) {
      errs() << "error: cannot open " << InputPath << "\n";
      return 2;
    }
    Input = &InputFile;
  }

  std::string Line;
  size_t Failures = 0;
  while (std::getline(*Input, Line)) {
    if (Line.empty())
      continue;

    auto Record = parseRecord(Line);
    if (!Record) {
      printError(outs(), -1, toString(Record.takeError()));
      ++Failures;
      continue;
    }

    LegalityQuery Query(Record->Opcode, Record->Types, Record->Mems,
                        Record->Imms);
    LegalizeActionStep Step = LI.getAction(Query);
    printResult(outs(), Record->Id, Step);
  }

  if (Failures != 0) {
    errs() << "error: " << Failures << " malformed query line(s)\n";
    return 1;
  }
  return 0;
}
