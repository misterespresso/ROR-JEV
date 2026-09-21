// Dump a .NET assembly's metadata as sorted, diffable text.
//
// Used to compare a fork build against the DLL whose behaviour the Phase 0 recordings
// validated (docs/09-FORK-BASE.md, E1). Commit hashes cannot establish that the fork is the
// validated artifact -- no commit ever declared the installed DLL's version -- so the
// comparison has to be made on what the assembly contains.
//
// Sections, each sorted so two dumps diff cleanly:
//   ASSEMBLYREF  what it links against
//   TYPE         every type it defines
//   METHOD       every method, as Type::Name/arity
//   MEMBERREF    every external member it calls (RoR2, Unity, BepInEx, MMHOOK hooks)
//   USERSTRING   every string literal -- for this mod that is the entire wire protocol:
//                query names, event names, JSON fragments, reason codes
//   ATTR         the BepInPlugin attribute arguments (GUID, name, version)
//
// Run with the .NET 10 SDK's file-based apps:
//   dotnet run tools/dllmeta.cs -- path/to/Assembly.dll > out.txt
#:property TargetFramework=net10.0

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection.Metadata;
using System.Reflection.Metadata.Ecma335;
using System.Reflection.PortableExecutable;
using System.Text;

bool ilMode = args.Contains("--il");
var paths = args.Where(a => a != "--il").ToArray();
if (paths.Length != 1) { Console.Error.WriteLine("usage: dllmeta [--il] <assembly.dll>"); return 2; }

using var fs = File.OpenRead(paths[0]);
using var pe = new PEReader(fs);
var md = pe.GetMetadataReader();

string TypeName(EntityHandle h) => h.Kind switch
{
    HandleKind.TypeDefinition => Qual(md.GetTypeDefinition((TypeDefinitionHandle)h)),
    HandleKind.TypeReference => QualRef(md.GetTypeReference((TypeReferenceHandle)h)),
    HandleKind.TypeSpecification => "<spec>",
    _ => "<" + h.Kind + ">",
};
string Qual(TypeDefinition t)
{
    var name = md.GetString(t.Name);
    var ns = md.GetString(t.Namespace);
    var decl = t.GetDeclaringType();
    if (!decl.IsNil) return Qual(md.GetTypeDefinition(decl)) + "+" + name;
    return string.IsNullOrEmpty(ns) ? name : ns + "." + name;
}
string QualRef(TypeReference t)
{
    var name = md.GetString(t.Name);
    var ns = md.GetString(t.Namespace);
    if (t.ResolutionScope.Kind == HandleKind.TypeReference)
        return QualRef(md.GetTypeReference((TypeReferenceHandle)t.ResolutionScope)) + "+" + name;
    return string.IsNullOrEmpty(ns) ? name : ns + "." + name;
}

var lines = new List<string>();

if (ilMode)
{
    // One line per method: ILHASH Type::Name/arity <hash of normalised IL>.
    //
    // Metadata equality cannot see a changed constant inside a method body, so this compares
    // bodies. Raw IL bytes are useless for that: tokens are table indices, and one extra
    // member reference in either assembly renumbers every token after it. So each operand is
    // resolved to a NAME, branch targets become instruction indices, and encoding choices a
    // compiler makes freely (br.s vs br, ldc.i4.s vs ldc.i4.7 vs ldc.i4) are collapsed. What
    // remains differs only if the code does.
    var opcodes = OpTable.Build();
    foreach (var th in md.TypeDefinitions)
    {
        var t = md.GetTypeDefinition(th);
        var q = Qual(t);
        foreach (var mh in t.GetMethods())
        {
            var m = md.GetMethodDefinition(mh);
            var arity = m.DecodeSignature(new ArityProvider(), null).ParameterTypes.Length;
            var id = $"{q}::{md.GetString(m.Name)}/{arity}";
            if (m.RelativeVirtualAddress == 0) { lines.Add($"ILHASH {id} <no-body>"); continue; }
            var body = pe.GetMethodBody(m.RelativeVirtualAddress);
            var norm = Il.Normalise(body.GetILBytes()!, opcodes, md, TypeName, Qual);
            var hash = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(
                Encoding.UTF8.GetBytes(norm)))[..16];
            lines.Add($"ILHASH {id} {hash}");
            if (Environment.GetEnvironmentVariable("DLLMETA_DUMP") is string want && id.Contains(want))
                Console.Error.WriteLine($"--- {id}\n{norm}");
        }
    }
    foreach (var l in lines.OrderBy(x => x, StringComparer.Ordinal)) Console.WriteLine(l);
    return 0;
}

foreach (var h in md.AssemblyReferences)
{
    var r = md.GetAssemblyReference(h);
    // Version is deliberately omitted: it moves with the SDK and tells us nothing about
    // behaviour. The NAME is what distinguishes BepInEx 5 from BepInEx 6.
    lines.Add("ASSEMBLYREF " + md.GetString(r.Name));
}

foreach (var h in md.TypeDefinitions)
{
    var t = md.GetTypeDefinition(h);
    var q = Qual(t);
    if (q == "<Module>") continue;
    lines.Add("TYPE " + q);
    foreach (var mh in t.GetMethods())
    {
        var m = md.GetMethodDefinition(mh);
        var sig = m.DecodeSignature(new ArityProvider(), null);
        lines.Add($"METHOD {q}::{md.GetString(m.Name)}/{sig.ParameterTypes.Length}");
    }
}

foreach (var h in md.MemberReferences)
{
    var r = md.GetMemberReference(h);
    var parent = r.Parent.Kind is HandleKind.TypeReference or HandleKind.TypeDefinition
        ? TypeName(r.Parent) : "<" + r.Parent.Kind + ">";
    // Compiler-generated generic instantiations produce noise that differs between compilers
    // without meaning anything; keep the member identity only.
    lines.Add($"MEMBERREF {parent}::{md.GetString(r.Name)}");
}

// User strings: walk the #US heap directly.
var us = md.GetUserString(MetadataTokens.UserStringHandle(1));
var handle = MetadataTokens.UserStringHandle(1);
while (!handle.IsNil)
{
    string s;
    try { s = md.GetUserString(handle); } catch { break; }
    if (s.Length > 0)
        lines.Add("USERSTRING " + s.Replace("\r", "\\r").Replace("\n", "\\n"));
    handle = md.GetNextHandle(handle);
}

foreach (var ch in md.CustomAttributes)
{
    var ca = md.GetCustomAttribute(ch);
    string ctorType = ca.Constructor.Kind switch
    {
        HandleKind.MemberReference => TypeName(md.GetMemberReference((MemberReferenceHandle)ca.Constructor).Parent),
        HandleKind.MethodDefinition => Qual(md.GetTypeDefinition(md.GetMethodDefinition((MethodDefinitionHandle)ca.Constructor).GetDeclaringType())),
        _ => "?",
    };
    if (!ctorType.EndsWith("BepInPlugin")) continue;
    var blob = md.GetBlobReader(ca.Value);
    blob.ReadUInt16();                          // prolog
    var parts = new List<string>();
    for (int i = 0; i < 3; i++) parts.Add(blob.ReadSerializedString() ?? "null");
    lines.Add("ATTR BepInPlugin " + string.Join(" | ", parts));
}

foreach (var l in lines.Distinct().OrderBy(x => x, StringComparer.Ordinal))
    Console.WriteLine(l);
return 0;

static class OpTable
{
    public static Dictionary<ushort, System.Reflection.Emit.OpCode> Build()
    {
        var d = new Dictionary<ushort, System.Reflection.Emit.OpCode>();
        foreach (var f in typeof(System.Reflection.Emit.OpCodes).GetFields())
            if (f.GetValue(null) is System.Reflection.Emit.OpCode op)
                d[(ushort)op.Value] = op;
        return d;
    }
}

static class Il
{
    public static string Normalise(byte[] il, Dictionary<ushort, System.Reflection.Emit.OpCode> ops,
        MetadataReader md, Func<EntityHandle, string> typeName, Func<TypeDefinition, string> qual)
    {
        // Pass 1: decode into (offset, name, operand, branch targets as byte offsets).
        var insns = new List<(int off, string name, string arg, int[] targets)>();
        int pos = 0;
        while (pos < il.Length)
        {
            int start = pos;
            ushort code = il[pos++];
            if (code == 0xFE) code = (ushort)(0xFE00 | il[pos++]);
            if (!ops.TryGetValue(code, out var op)) { insns.Add((start, $"?{code:X}", "", [])); continue; }
            string name = op.Name!, arg = "";
            int[] targets = [];
            switch (op.OperandType)
            {
                case System.Reflection.Emit.OperandType.InlineNone: break;
                case System.Reflection.Emit.OperandType.ShortInlineBrTarget:
                    { int o = (sbyte)il[pos]; pos += 1; targets = [pos + o]; break; }
                case System.Reflection.Emit.OperandType.InlineBrTarget:
                    { int o = BitConverter.ToInt32(il, pos); pos += 4; targets = [pos + o]; break; }
                case System.Reflection.Emit.OperandType.InlineSwitch:
                    {
                        int n = BitConverter.ToInt32(il, pos); pos += 4;
                        var offs = new int[n];
                        for (int i = 0; i < n; i++) { offs[i] = BitConverter.ToInt32(il, pos); pos += 4; }
                        targets = offs.Select(o => pos + o).ToArray();
                        break;
                    }
                case System.Reflection.Emit.OperandType.ShortInlineI:
                    arg = (name == "ldc.i4.s" ? (int)(sbyte)il[pos] : il[pos]).ToString(); pos += 1; break;
                case System.Reflection.Emit.OperandType.ShortInlineVar:
                    arg = il[pos].ToString(); pos += 1; break;
                case System.Reflection.Emit.OperandType.InlineVar:
                    arg = BitConverter.ToUInt16(il, pos).ToString(); pos += 2; break;
                case System.Reflection.Emit.OperandType.InlineI:
                    arg = BitConverter.ToInt32(il, pos).ToString(); pos += 4; break;
                case System.Reflection.Emit.OperandType.InlineI8:
                    arg = BitConverter.ToInt64(il, pos).ToString(); pos += 8; break;
                case System.Reflection.Emit.OperandType.ShortInlineR:
                    arg = BitConverter.ToSingle(il, pos).ToString("R"); pos += 4; break;
                case System.Reflection.Emit.OperandType.InlineR:
                    arg = BitConverter.ToDouble(il, pos).ToString("R"); pos += 8; break;
                default: // every token-bearing operand
                    arg = Resolve(BitConverter.ToInt32(il, pos), md, typeName, qual); pos += 4; break;
            }
            (name, arg) = Canonical(name, arg);
            insns.Add((start, name, arg, targets));
        }

        // Pass 2: branch targets as instruction INDICES, so a compiler picking a longer or
        // shorter encoding for some earlier instruction does not register as a difference.
        var index = new Dictionary<int, int>();
        for (int i = 0; i < insns.Count; i++) index[insns[i].off] = i;
        var sb = new StringBuilder();
        foreach (var (_, name, arg, targets) in insns)
        {
            sb.Append(name);
            if (arg.Length > 0) sb.Append(' ').Append(arg);
            foreach (var t in targets) sb.Append(" @").Append(index.TryGetValue(t, out var ix) ? ix : -1);
            sb.Append('\n');
        }
        return sb.ToString();
    }

    // Collapse encodings a compiler may choose freely. `br.s` and `br` do the same thing;
    // `ldc.i4.7`, `ldc.i4.s 7` and `ldc.i4 7` all push 7.
    static (string, string) Canonical(string name, string arg)
    {
        if (name.EndsWith(".s")) name = name[..^2];
        if (name.StartsWith("ldc.i4.") && name != "ldc.i4.s")
        {
            var tail = name["ldc.i4.".Length..];
            if (tail == "m1" || tail == "M1") return ("ldc.i4", "-1");
            if (int.TryParse(tail, out var n)) return ("ldc.i4", n.ToString());
        }
        foreach (var fam in new[] { "ldloc", "stloc", "ldarg", "starg" })
            if (name.StartsWith(fam + ".") && int.TryParse(name[(fam.Length + 1)..], out var n))
                return (fam, n.ToString());
        return (name, arg);
    }

    static string Resolve(int token, MetadataReader md, Func<EntityHandle, string> typeName,
        Func<TypeDefinition, string> qual)
    {
        // User strings arrive as 0x70 tokens, which EntityHandle cannot represent.
        if ((token >> 24) == 0x70)
            return "\"" + md.GetUserString(MetadataTokens.UserStringHandle(token & 0xFFFFFF)) + "\"";
        var h = MetadataTokens.EntityHandle(token);
        try
        {
            switch (h.Kind)
            {
                case HandleKind.MethodDefinition:
                    {
                        var m = md.GetMethodDefinition((MethodDefinitionHandle)h);
                        return qual(md.GetTypeDefinition(m.GetDeclaringType())) + "::" + md.GetString(m.Name);
                    }
                case HandleKind.FieldDefinition:
                    {
                        var f = md.GetFieldDefinition((FieldDefinitionHandle)h);
                        return qual(md.GetTypeDefinition(f.GetDeclaringType())) + "::" + md.GetString(f.Name);
                    }
                case HandleKind.MemberReference:
                    {
                        var r = md.GetMemberReference((MemberReferenceHandle)h);
                        var p = r.Parent.Kind is HandleKind.TypeReference or HandleKind.TypeDefinition
                            ? typeName(r.Parent) : "<" + r.Parent.Kind + ">";
                        return p + "::" + md.GetString(r.Name);
                    }
                case HandleKind.MethodSpecification:
                    {
                        var s = md.GetMethodSpecification((MethodSpecificationHandle)h);
                        return "spec:" + Resolve(MetadataTokens.GetToken(s.Method), md, typeName, qual);
                    }
                case HandleKind.TypeDefinition:
                case HandleKind.TypeReference:
                    return typeName(h);
                case HandleKind.TypeSpecification:
                    return "<typespec>";
                case HandleKind.StandaloneSignature:
                    return "<sig>";
            }
        }
        catch { }
        return $"<tok {token >> 24:X2}>";
    }
}

// Only parameter COUNT is needed to tell overloads apart for a diff.
sealed class ArityProvider : ISignatureTypeProvider<int, object?>
{
    public int GetArrayType(int e, ArrayShape s) => 0;
    public int GetByReferenceType(int e) => 0;
    public int GetFunctionPointerType(MethodSignature<int> s) => 0;
    public int GetGenericInstantiation(int g, System.Collections.Immutable.ImmutableArray<int> a) => 0;
    public int GetGenericMethodParameter(object? c, int i) => 0;
    public int GetGenericTypeParameter(object? c, int i) => 0;
    public int GetModifiedType(int m, int u, bool r) => 0;
    public int GetPinnedType(int e) => 0;
    public int GetPointerType(int e) => 0;
    public int GetPrimitiveType(PrimitiveTypeCode t) => 0;
    public int GetSZArrayType(int e) => 0;
    public int GetTypeFromDefinition(MetadataReader r, TypeDefinitionHandle h, byte k) => 0;
    public int GetTypeFromReference(MetadataReader r, TypeReferenceHandle h, byte k) => 0;
    public int GetTypeFromSpecification(MetadataReader r, object? c, TypeSpecificationHandle h, byte k) => 0;
}
