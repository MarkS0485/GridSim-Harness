using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Serialization;
using GridSim.Core.Diagnostics;
using GridSim.Core.Abstractions;
using GridSim.Core.IO;
using GridSim.Core.IO.Json;
using GridSim.Core.IO.Matpower;
using GridSim.Core.PowerFlow;
using GridSim.Gpu;
using ILGPU.Runtime;

// Paths: dense / sparse (the CPU engines, the canonical contract), gpu-dense / gpu-sparse (the CUDA linear solvers
// behind the same Newton engines; outside the bit-identity contract). The case is a MATPOWER .m file, or a GridSim
// JSON model directory (the "models" tier; system.json, buses.json, ...).
string[] paths = { "dense", "sparse", "gpu-dense", "gpu-sparse" };
if (args.Length != 4 || !paths.Contains(args[1]))
{
    Console.Error.WriteLine("Usage: BenchmarkDriver case.m|model-dir dense|sparse|gpu-dense|gpu-sparse output-directory gridsim-commit");
    return 2;
}

var (casePath, path, output, commit) = (args[0], args[1], args[2], args[3]);
bool gpu = path.StartsWith("gpu-", StringComparison.Ordinal);
if (gpu && !GpuContext.IsAvailableFor(AcceleratorType.Cuda))
{
    // Refuse rather than time the CPU fallback under a GPU label.
    Console.Error.WriteLine($"No CUDA device: {GpuContext.DescriptionFor(AcceleratorType.Cuda)}");
    return 3;
}
Directory.CreateDirectory(output);
var options = new PowerFlowOptions
{
    FlatStart = true,
    EnforceQLimits = false,
    Tolerance = 1e-8,
    MaxIterations = 50,
};
var json = new JsonSerializerOptions
{
    NumberHandling = JsonNumberHandling.AllowNamedFloatingPointLiterals,
    PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
};

GridSim.Core.Model.GridModel Load() => Directory.Exists(casePath)
    ? JsonGridLoader.LoadDirectory(casePath)
    : MatpowerParser.ParseFile(casePath);

IPowerFlowSolver Solver() => path switch
{
    "dense" => new NewtonRaphsonPowerFlow(),
    "sparse" => new SparseNewtonRaphsonPowerFlow(),
    "gpu-dense" => new NewtonRaphsonPowerFlow(new GpuDenseLinearSolver(AcceleratorType.Cuda)),
    _ => new SparseNewtonRaphsonPowerFlow(new GpuSparseLinearSolver(accel: AcceleratorType.Cuda)),
};

Solver().Solve(Load(), options);
var saves = new List<Action>();
for (int attempt = 1; attempt <= 10; attempt++)
{
    var model = Load();
    var solver = Solver();
    var started = DateTimeOffset.UtcNow;
    GpuSolveCount before = GpuSolveCounters.Snapshot();
    long start = Stopwatch.GetTimestamp();
    var result = solver.Solve(model, options);
    double elapsedMs = Stopwatch.GetElapsedTime(start).TotalMilliseconds;
    GpuSolveCount solves = GpuSolveCounters.Snapshot() - before;
    object? gpuSolves = gpu
        ? new { solves.Total, solves.OnDevice, solves.GaveUpToCpu, solves.OnHost, solves.RanEntirelyOnCpu }
        : null;
    var row = new { attempt, elapsedMs, started, result, gpuSolves };
    int index = attempt;
    saves.Add(() =>
    {
        File.WriteAllText(Path.Combine(output, $"attempt-{index:D2}.json"), JsonSerializer.Serialize(row, json));
        SolveExport.WriteJson(Path.Combine(output, $"export-{index:D2}.json"), model, result,
            new SolveTrace(), new SolveExportMeta(Path.GetFileName(casePath.TrimEnd('/')), path, commit,
                started.ToString("O"), elapsedMs, Environment.ProcessorCount + " logical CPUs"));
    });
}
foreach (var save in saves)
    save();
return 0;
