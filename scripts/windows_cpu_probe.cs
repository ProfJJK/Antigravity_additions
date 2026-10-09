// CPU-only, one-shot LibreHardwareMonitor 0.9.6 probe. No GUI, driver installation,
// fan control, web server, startup registration, or persistent settings writes.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using LibreHardwareMonitor.Hardware;

internal static class CoChemCpuProbe
{
    private static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        var json = new JavaScriptSerializer();
        if (args.Length == 1 && args[0] == "--capabilities")
        {
            Console.WriteLine(json.Serialize(new {
                schema = "cochem-cpu-probe-capabilities/1",
                library_version = typeof(Computer).Assembly.GetName().Version.ToString(),
                cpu_only = true, supported_cpu = "Intel direct core/package sensors",
                installs_driver = false, writes_settings = false
            }));
            return 0;
        }
        if (args.Length != 2 || args[0] != "--nonce" || !Regex.IsMatch(args[1], "\\A[0-9a-f]{32}\\z"))
        {
            Console.Error.WriteLine("Expected --nonce followed by 32 lowercase hex characters.");
            return 2;
        }
        try
        {
            if (typeof(Computer).Assembly.GetName().Version.ToString() != "0.9.6.0")
                throw new InvalidOperationException("The probe requires the reviewed LibreHardwareMonitor 0.9.6 library.");
            if (!LibreHardwareMonitor.PawnIo.PawnIo.IsInstalled)
                throw new InvalidOperationException("The reviewed PawnIO driver must already be installed; the probe never installs it.");
            var readings = new List<object>();
            var computer = new Computer { IsCpuEnabled = true };
            try
            {
                computer.Open();
                foreach (IHardware hardware in computer.Hardware)
                {
                    if (hardware.HardwareType != HardwareType.Cpu) continue;
                    hardware.Update();
                    Thread.Sleep(100);
                    hardware.Update();
                    foreach (ISensor sensor in hardware.Sensors)
                    {
                        // LHM also labels headroom-to-TjMax and cached aggregates
                        // as Temperature. Only these direct MSR readings become
                        // null when the FINAL update fails (IntelCpu.cs v0.9.6).
                        if (sensor.SensorType != SensorType.Temperature
                            || !sensor.Identifier.ToString().StartsWith("/intelcpu/", StringComparison.Ordinal)
                            || !(sensor.Name == "CPU Package" || Regex.IsMatch(sensor.Name, "\\A(?:CPU Core|P-Core|E-Core|Core) #[0-9]+\\z"))) continue;
                        if (!sensor.Value.HasValue)
                            throw new InvalidOperationException("A direct CPU temperature sensor was unavailable on the final update.");
                        double value = sensor.Value.Value;
                        if (Double.IsNaN(value) || Double.IsInfinity(value) || value < -50 || value > 150)
                            throw new InvalidOperationException("CPU sensor returned an invalid temperature.");
                        readings.Add(new { identifier = sensor.Identifier.ToString(), name = sensor.Name, celsius = value });
                        if (readings.Count > 256) throw new InvalidOperationException("Too many CPU sensor readings.");
                    }
                }
            }
            finally { computer.Close(); }
            if (readings.Count == 0) throw new InvalidOperationException("No identifiable CPU temperature sensor was available.");
            Console.WriteLine(json.Serialize(new {
                schema = "cochem-cpu-temperature/1", provider = "LibreHardwareMonitorLib",
                library_version = "0.9.6.0", nonce = args[1],
                sampled_at_unix_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(), sensors = readings
            }));
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
            return 1;
        }
    }
}
