"""Compile C# contract, session, learning and calendar rules without NinjaTrader APIs.

Requires a .NET 8 SDK. This validates the extracted helpers, not the full strategy.
"""

import argparse
import json
import os
from pathlib import Path
import random
import string
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bot.contracts import contract_key, same_contract
from bot.strategy import runner_stop


def extract_method(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dotnet", default="dotnet")
    args = parser.parse_args()
    source = (ROOT / "ninjatrader/MNQPlanPaper.cs").read_text()
    methods = "\n".join(extract_method(source, signature) for signature in (
        "private static string MnqContractKey", "private static bool SameMnqContract"))
    methods += "\n"+"\n".join(extract_method(source, signature) for signature in (
        "private bool IsRolling", "private bool UseTrendRunner", "private int MaximumHoldMinutes", "private static double RunnerStop", "private DateTime TradingDay", "private static bool Overnight", "private bool MarketOpen",
        "private DateTime FlattenAt", "private string ScheduledExitReason", "private DateTime LastEntryAt", "private double MaximumStopAtr", "private string QualityArm",
        "private double GapSeconds", "private double RealizedFor", "private QualityState QualityFor", "private static bool ConnectionShouldLatch",
        "private Dictionary<DateTime, SessionRule> LoadCalendar", "private DateTime CalendarTime",
        "private List<Tuple<DateTime,DateTime>> DateWindows"))
    scaffolding = "\n".join(extract_method(source, signature) for signature in (
        "private class SessionRule", "private class ResultSample", "private class QualityState", "private class LotLedger"))
    scaffolding += '''
    private enum MNQPaperArm {P0,C1,R1,R2}
    private MNQPaperArm Arm=MNQPaperArm.R2;
    private enum MNQPaperExit {Fixed,TrendRunner}
    private MNQPaperExit ExitProfile=MNQPaperExit.Fixed;
    private SessionRule session;
    private DateTime day;
    private bool entryOvernight,AdaptiveQuality=true;
    private double RollingMinEfficiency=.4;
    private int PaperContracts=1;
    private System.Collections.Generic.List<ResultSample> results=new System.Collections.Generic.List<ResultSample>();
    private TimeZoneInfo easternZone=TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time");
    private string calendarHash;
    '''
    values = [None, "", "MNQ-TEST", "NQ DEC26", "MNQ DEC2026", "MNQ ##-##"]
    pairs = []
    for label, month in (("MAR", "03"), ("JUN", "06"), ("SEP", "09"), ("DEC", "12")):
        for year in range(100):
            numeric, alias = f"MNQ {month}-{year:02}", f"MNQ {label}{year:02}"
            values.extend((numeric, alias, f" mnq {label.lower()}{year:02} "))
            pairs.extend(((alias, numeric), (numeric, f"MNQ {month}-{(year+1)%100:02}"), (numeric, "NQ DEC26")))
    seeded = random.Random(7102026)
    values += ["".join(seeded.choices(string.ascii_letters + string.digits + " -#", k=seeded.randrange(24))) for _ in range(2000)]
    checks = []
    for value in values:
        checks.append(f"if(MnqContractKey({json.dumps(value)})!={json.dumps(contract_key(value))}) throw new Exception(\"Contract key mismatch\");")
    for left, right in pairs:
        expected = str(same_contract(left, right)).lower()
        checks.append(f"if(SameMnqContract({json.dumps(left)},{json.dumps(right)})!={expected}) throw new Exception(\"Expiry match mismatch\");")
    checks += ['''
        day=new DateTime(2026,1,5);
        session=new SessionRule {Day=day,Contract="MNQ 12-26",NewsFlags="",Roll=false,LateNews=false,Open=new TimeSpan(9,30,0),Close=new TimeSpan(16,0,0),GlobexOpen=day.AddDays(-1).AddHours(18),GlobexClose=day.AddHours(17)};
        if(TradingDay(day.AddDays(-1).AddHours(18))!=day || TradingDay(day)!=day || TradingDay(day.AddHours(18))!=day.AddDays(1)) throw new Exception("Futures risk date mapping");
        if(!MarketOpen(session,session.GlobexOpen) || MarketOpen(session,session.GlobexClose) || FlattenAt()!=day.AddHours(16).AddMinutes(55)) throw new Exception("CME open/close/flatten boundaries");
        if(!Overnight(day.AddHours(2)) || Overnight(day.AddHours(11))) throw new Exception("Overnight profile mapping");
        entryOvernight=true;if(MaximumStopAtr()!=.1) throw new Exception("Overnight risk");
        entryOvernight=false;if(MaximumStopAtr()!=.2) throw new Exception("Daytime risk");
        for(int i=0;i<8;i++) results.Add(new ResultSample {Id=i.ToString(),Arm="R2_OVERNIGHT",ClosedAt=day.AddHours(1).AddMinutes(i),Direction=1,Net=-1,QualityEligible=true});
        if(Math.Abs(QualityFor(1,day.AddHours(2)).Minimum-.7)>1e-10 || QualityFor(1,day.AddHours(11)).Count!=0) throw new Exception("Separate causal overnight/daytime learning");
        session.GlobexBreaks.Add(Tuple.Create(day.AddHours(8),day.AddHours(8).AddMinutes(15)));
        if(GapSeconds(day.AddHours(8).AddSeconds(-30),day.AddHours(8).AddMinutes(15).AddSeconds(30))!=60) throw new Exception("Reviewed break excluded from gap timeout");
        if(LastEntryAt(day.AddHours(7))!=day.AddHours(7).AddMinutes(55) || FlattenAt(day.AddHours(7))!=day.AddHours(7).AddMinutes(59) || ScheduledExitReason(day.AddHours(7))!="MARKET_BREAK_EXIT") throw new Exception("Reviewed-break entry and flatten deadlines");
        if(LastEntryAt(day.AddHours(9))!=day.AddHours(16).AddMinutes(45) || FlattenAt(day.AddHours(9))!=day.AddHours(16).AddMinutes(55) || ScheduledExitReason(day.AddHours(9))!="TIME_EXIT") throw new Exception("Post-break close deadlines recover");
        ExitProfile=MNQPaperExit.TrendRunner;entryOvernight=true;if(MaximumHoldMinutes()!=45 || QualityArm(true)!="R2_RUNNER_OVERNIGHT" || QualityFor(1,day.AddHours(2)).Count!=0) throw new Exception("Runner overnight hold and isolated learning");
        entryOvernight=false;if(MaximumHoldMinutes()!=90) throw new Exception("Runner daytime hold");ExitProfile=MNQPaperExit.Fixed;
        var lots=new LotLedger();lots.Enter(20000,4);lots.Enter(20001,6);
        double part=lots.Exit(20005,4,1,1.5);if(lots.Complete(false,10) || Math.Abs(part-29.2)>1e-7) throw new Exception("Partial exit cash and pending position");
        lots.Exit(20003,6,1,1.5);if(!lots.Complete(false,10) || lots.Complete(true,10) || lots.Complete(false,11) || Math.Abs(lots.Net-49)>1e-7) throw new Exception("Weighted partial-fill accounting and completion");
        results.Add(new ResultSample {Id="risk",Arm="RISK:R2_RTH",Net=6,ClosedAt=day.AddHours(3)});
        results.Add(new ResultSample {Id="learn",Arm="LEARNING:R2_RTH",Net=6,ClosedAt=day.AddHours(3)});
        if(RealizedFor(day,day.AddHours(4))!=-2) throw new Exception("Learning rows must not double-count restored cash risk");
        session.TradeEnabled=false;if(GapSeconds(day.AddDays(-1).AddHours(22),day.AddHours(9).AddMinutes(30))!=0 || GapSeconds(day.AddHours(10),day.AddHours(10).AddMinutes(2))!=120) throw new Exception("Cash-only warmup coverage");session.TradeEnabled=true;
        PaperContracts=2;if(QualityArm(true)!="R2_OVERNIGHT_Q2" || QualityFor(1,day.AddHours(2)).Count!=0) throw new Exception("Separate size learning");PaperContracts=1;
        Arm=MNQPaperArm.R1;if(MarketOpen(session,day.AddHours(2)) || TradingDay(day.AddHours(18))!=day) throw new Exception("R1 session regression");
    ''']
    for _ in range(1000):
        d=seeded.choice((-1,1));fill=20000;risk=seeded.randrange(4,100)*.25
        peak=fill+d*seeded.randrange(0,200)*.25
        stop=fill+d*seeded.randrange(-100,100)*.25
        executable=peak-d*seeded.randrange(0,80)*.25
        expected=runner_stop(d,fill,risk,peak,stop,executable)
        checks.append(f'if(RunnerStop({d},{fill},{risk},{peak},{stop},{executable})!={expected}) throw new Exception("Runner stop parity");')
    for _ in range(1000):
        q=seeded.choice((1,2,5,10));side=seeded.choice((-1,1))
        entries=[20000+seeded.randrange(-8,9)*.25 for n in range(q)]
        exits=[20000+seeded.randrange(-40,41)*.25 for n in range(q)]
        expected=side*(sum(exits)-sum(entries))*2-1.5*q
        body='var book=new LotLedger();'+''.join(f'book.Enter({v},1);' for v in entries)+''.join(f'book.Exit({v},1,{side},1.5);' for v in exits)
        checks.append('{'+body+f'if(Math.Abs(book.Net-({expected}))>1e-7 || !book.Complete(false,{q})) throw new Exception("Sized partial-fill P&L identity");'+'}')
    for observed in (False,True):
        for owns in (False,True):
            for lost in (False,True):
                for previous in (False,True):
                    inputs=",".join(str(x).lower() for x in (observed,owns,lost,previous))
                    expected=str((observed or owns) and (lost or previous)).lower()
                    checks.append(f'if(ConnectionShouldLatch({inputs})!={expected}) throw new Exception("Startup/disconnect classification");')
    calendar_path=ROOT/"ninjatrader/calendars/mnq-dec26-full-session-2026-10-07-30/MNQCalendar.csv"
    checks.append(f'Arm=MNQPaperArm.R2;var approved=LoadCalendar({json.dumps(str(calendar_path))});if(approved.Count!=49 || approved.Values.Count(s=>s.TradeEnabled)!=18) throw new Exception("Native calendar loader");')
    checks.append('if(approved[new DateTime(2026,10,14)].GlobexNews.All(w=>w.Item1!=new DateTime(2026,10,14,8,25,0))) throw new Exception("Native 08:30 macro pause");')
    with tempfile.TemporaryDirectory(prefix="mnq-native-contracts-") as directory:
        work = Path(directory)
        (work / "Check.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>')
        (work / "NuGet.Config").write_text('<configuration><packageSources><clear /></packageSources></configuration>')
        (work / "Program.cs").write_text("using System; using System.Linq; using System.Collections.Generic; using System.IO; using System.Globalization; using System.Security.Cryptography; class Program {" + scaffolding + methods + "static void Main(){new Program().Run();} void Run(){" + "\n".join(checks) + f'Console.WriteLine("Compiled C# contract/session/learning/calendar checks passed: {len(checks)} cases plus boundary assertions.");' + "}}")
        environment = dict(os.environ, DOTNET_CLI_HOME=str(work / "cli"), DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1")
        subprocess.run([args.dotnet, "run", "--project", str(work / "Check.csproj"), "--configuration", "Release"], check=True, env=environment)


if __name__ == "__main__":
    main()
