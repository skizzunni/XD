// NinjaTrader 8 source: import into NinjaScript Editor > Strategies.
// Simulation/playback only. Requires an explicit frozen session/news/roll calendar.
// Native compilation and playback acceptance must be completed on Windows.
using System;
using System.Collections.Generic;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;

namespace NinjaTrader.NinjaScript.Strategies
{
    public enum MNQPaperStage { Funded, Evaluation }
    public enum MNQPaperArm { P0, C1, R1, R2 }

    public class MNQPlanPaper : Strategy
    {
        private class SessionRule
        {
            public DateTime Day;
            public TimeSpan Open, Close;
            public string Contract, NewsFlags;
            public bool Roll, LateNews;
            public bool TradeEnabled = true;
            public List<Tuple<TimeSpan,TimeSpan>> NewsWindows = new List<Tuple<TimeSpan,TimeSpan>>();
            public DateTime GlobexOpen, GlobexClose;
            public List<Tuple<DateTime,DateTime>> GlobexNews = new List<Tuple<DateTime,DateTime>>();
            public List<Tuple<DateTime,DateTime>> GlobexBreaks = new List<Tuple<DateTime,DateTime>>();
        }
        private class Candle
        {
            public DateTime End;
            public double Open, High, Low, Close;
        }
        private class ResultSample
        {
            public string Id, Arm;
            public DateTime ClosedAt;
            public int Direction;
            public double Net;
            public bool QualityEligible;
        }
        private class QualityState
        {
            public int Count;
            public double Net, Minimum;
            public bool Tightened;
        }
        private Dictionary<DateTime, SessionRule> calendar;
        private Dictionary<DateTime, List<Candle>> candles;
        private Dictionary<DateTime, List<Candle>> extendedCandles;
        private HashSet<DateTime> invalidDays;
        private Queue<double> ranges;
        private TimeZoneInfo platformZone, easternZone;
        private SessionRule session;
        private DateTime day, lastTickTime, lastSignalEnd;
        private DateTime gapFormed, gapExpires;
        private double atr, previousClose, previousPrice, entryFill, stopPrice, initialRisk;
        private double gapMidpoint, gapStop, dayRealized, submittedReference, signalM, mfe, mae;
        private string previousContract, ledgerPath, tickPath, calendarHash, exitReason, reviewPath, tradeKey;
        private StreamWriter tickWriter;
        private int direction, openQuantity;
        private bool signalFrozen, attempted, blocked, gapChosen, breakEvenArmed, exitPending;
        private bool disconnected, startupChecked;
        private bool stopEverAccepted;
        private long tradeTicks;
        private Order stopOrder;
        private Order entryOrder;
        private DateTime entryDeadline;
        private long eventSequence, tickSequence;
        private HashSet<string> executionIds;
        private System.Threading.Timer heartbeat;
        private bool staleFaultRaised;
        private bool ownershipDenied, entrySubmissionPending;
        private FileStream ownerLease;
        private string riskLedgerPath;
        private List<ResultSample> results;
        private DateTime entryTime, lastExitTime, lastStatusTime;
        private double rollingReference, rollingEfficiency, entryQualityMinimum;
        private bool entryOvernight;
        private bool marketFeedObserved;
        private bool quoteWaitLogged;

        private bool IsRolling { get { return Arm==MNQPaperArm.R1 || Arm==MNQPaperArm.R2; } }
        private DateTime TradingDay(DateTime et)
        {
            return Arm==MNQPaperArm.R2 && et.TimeOfDay>=new TimeSpan(18,0,0)?et.Date.AddDays(1):et.Date;
        }
        private static bool Overnight(DateTime et)
        {
            return et.TimeOfDay<new TimeSpan(9,30,0) || et.TimeOfDay>=new TimeSpan(16,0,0);
        }
        private bool MarketOpen(SessionRule rule,DateTime et)
        {
            if(rule==null) return false;
            return Arm==MNQPaperArm.R2?rule.GlobexOpen<=et && et<rule.GlobexClose
                && !rule.GlobexBreaks.Any(w=>w.Item1<=et && et<w.Item2)
                :et.Date==rule.Day && rule.Open<=et.TimeOfDay && et.TimeOfDay<rule.Close;
        }
        private DateTime FlattenAt(DateTime? at=null)
        {
            if(Arm!=MNQPaperArm.R2) return day.AddHours(15).AddMinutes(Arm==MNQPaperArm.P0?59:55);
            DateTime now=at.HasValue?at.Value:session.GlobexOpen,deadline=session.GlobexClose.AddMinutes(-5);
            foreach(var w in session.GlobexBreaks) if(w.Item2>now && w.Item1.AddMinutes(-1)<deadline) deadline=w.Item1.AddMinutes(-1);
            return deadline;
        }
        private string ScheduledExitReason(DateTime now)
        {
            return Arm==MNQPaperArm.R2 && FlattenAt(now)<session.GlobexClose.AddMinutes(-5)?"MARKET_BREAK_EXIT":"TIME_EXIT";
        }
        private DateTime LastEntryAt(DateTime now)
        {
            DateTime deadline=session.GlobexClose.AddMinutes(-15);
            foreach(var w in session.GlobexBreaks) if(w.Item2>now && w.Item1.AddMinutes(-5)<deadline) deadline=w.Item1.AddMinutes(-5);
            return deadline;
        }
        private double MaximumStopAtr() { return Arm==MNQPaperArm.R2 && entryOvernight?0.10:0.20; }
        private string QualityArm(bool night) { return Arm==MNQPaperArm.R2?"R2_"+(night?"OVERNIGHT":"RTH"):"R1"; }
        private static bool ConnectionShouldLatch(bool observed,bool ownsOrders,bool lost,bool previouslyLost)
        {
            return (observed || ownsOrders) && (lost || previouslyLost);
        }
        private double GapSeconds(DateTime previous,DateTime current)
        {
            double seconds=(current-previous).TotalSeconds;
            if(Arm==MNQPaperArm.R2 && session!=null) foreach(var w in session.GlobexBreaks)
            {
                DateTime a=previous>w.Item1?previous:w.Item1,b=current<w.Item2?current:w.Item2;
                if(b>a) seconds-=(b-a).TotalSeconds;
            }
            return seconds;
        }

        [NinjaScriptProperty]
        [Display(Name="Account stage", GroupName="Account", Order=0)]
        public MNQPaperStage AccountStage { get; set; }
        [NinjaScriptProperty]
        [Display(Name="Strategy arm", GroupName="Strategy", Order=0)]
        public MNQPaperArm Arm { get; set; }
        [NinjaScriptProperty, Range(0.001, 1.0)]
        [Display(Name="R1/R2 rolling momentum / ATR", GroupName="Strategy", Order=3)]
        public double RollingMomentumThreshold { get; set; }
        [NinjaScriptProperty, Range(0.01, 0.85)]
        [Display(Name="R1/R2 daytime trend efficiency", GroupName="Strategy", Order=4)]
        public double RollingMinEfficiency { get; set; }
        [NinjaScriptProperty]
        [Display(Name="R1/R2 adaptive quality filter", GroupName="Strategy", Order=5)]
        public bool AdaptiveQuality { get; set; }
        [NinjaScriptProperty, Range(1, 10000)]
        [Display(Name="Session loss limit ($)", GroupName="Account", Order=1)]
        public double SessionLossLimit { get; set; }
        [NinjaScriptProperty, Range(1, 10000)]
        [Display(Name="Evaluation daily profit target ($)", GroupName="Account", Order=2)]
        public double EvaluationProfitTarget { get; set; }
        [NinjaScriptProperty]
        [Display(Name="Use break-even protection", GroupName="Account", Order=3)]
        public bool UseBreakEven { get; set; }
        [NinjaScriptProperty, Range(0.1, 10)]
        [Display(Name="Break-even trigger (initial R)", GroupName="Account", Order=4)]
        public double BreakEvenTriggerR { get; set; }
        [NinjaScriptProperty, Range(0, 100)]
        [Display(Name="Round-trip fees per contract ($)", GroupName="Costs", Order=0)]
        public double RoundTurnFees { get; set; }
        [NinjaScriptProperty, Range(0, 20)]
        [Display(Name="Frozen spread (ticks)", GroupName="Costs", Order=1)]
        public int SpreadTicks { get; set; }
        [NinjaScriptProperty, Range(0, 20)]
        [Display(Name="Slippage per side (ticks)", GroupName="Costs", Order=2)]
        public int SlippagePerSide { get; set; }
        [NinjaScriptProperty, Range(0, 20)]
        [Display(Name="Entry slippage cap (ticks)", GroupName="Costs", Order=3)]
        public int EntrySlippageCap { get; set; }
        [NinjaScriptProperty]
        [Display(Name="Actual costs calibrated", GroupName="Costs", Order=4)]
        public bool CostsCalibrated { get; set; }
        [NinjaScriptProperty, Range(0.01, 1)]
        [Display(Name="Long threshold", GroupName="Strategy", Order=1)]
        public double LongThreshold { get; set; }
        [NinjaScriptProperty, Range(0.01, 1)]
        [Display(Name="Short threshold", GroupName="Strategy", Order=2)]
        public double ShortThreshold { get; set; }
        [NinjaScriptProperty, Range(1, 300)]
        [Display(Name="Maximum tick gap (seconds)", GroupName="Data", Order=0)]
        public int MaxTickGapSeconds { get; set; }
        [NinjaScriptProperty]
        [Display(Name="Platform display time zone ID", GroupName="Data", Order=1)]
        public string PlatformTimeZoneId { get; set; }
        [NinjaScriptProperty]
        [Display(Name="Frozen calendar CSV", GroupName="Data", Order=2)]
        public string CalendarFile { get; set; }
        [NinjaScriptProperty]
        [Display(Name="Allow historical orders (Analyzer only)", GroupName="Research", Order=0)]
        public bool AllowHistoricalOrders { get; set; }
        [NinjaScriptProperty]
        [Display(Name="Paper run ID", GroupName="Research", Order=1)]
        public string PaperRunId { get; set; }

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "MNQPlanPaper";
                Description = "R2 full CME session / R1 cash-session scanner, P0/C1 baselines; one MNQ contract, paper accounts only.";
                Calculate = Calculate.OnBarClose;
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = false;
                IsFillLimitOnTouch = false;
                StartBehavior = StartBehavior.WaitUntilFlat;
                RealtimeErrorHandling = RealtimeErrorHandling.StopCancelClose;
                ConnectionLossHandling = ConnectionLossHandling.KeepRunning;
                BarsRequiredToTrade = 6;
                Slippage = 1;
                AccountStage = MNQPaperStage.Funded;
                Arm = MNQPaperArm.R2;
                RollingMomentumThreshold = 0.05;
                RollingMinEfficiency = 0.40;
                AdaptiveQuality = true;
                SessionLossLimit = 100;
                EvaluationProfitTarget = 750;
                UseBreakEven = true;
                BreakEvenTriggerR = 1;
                RoundTurnFees = 1; // Provisional; replace with actual account schedule.
                SpreadTicks = 1;
                SlippagePerSide = 1;
                EntrySlippageCap = 4;
                CostsCalibrated = false;
                LongThreshold = ShortThreshold = 0.10;
                MaxTickGapSeconds = 90;
                PlatformTimeZoneId = "Eastern Standard Time";
                CalendarFile = Path.Combine(NinjaTrader.Core.Globals.UserDataDir, "MNQCalendar.csv");
                AllowHistoricalOrders = false;
                PaperRunId = "r2-sim101-001";
            }
            else if (State == State.Configure)
            {
                if(String.IsNullOrWhiteSpace(PaperRunId) || PaperRunId.Any(c=>
                    !(c>='a'&&c<='z' || c>='A'&&c<='Z' || c>='0'&&c<='9' || c=='-')))
                    throw new InvalidOperationException("Paper run ID must contain only ASCII letters, numbers and hyphens, exactly matching the dashboard.");
                Print("MNQPlanPaper startup: Paper run ID="+PaperRunId+"; logs="
                    +Path.Combine(NinjaTrader.Core.Globals.UserDataDir,"MNQPaper",PaperRunId));
                if (BarsPeriod.BarsPeriodType != BarsPeriodType.Minute || BarsPeriod.Value != 5)
                    throw new InvalidOperationException("Primary series must be 5-minute MNQ. Received "
                        +BarsPeriod.BarsPeriodType+" value="+BarsPeriod.Value+". Set Type=Minute, Value=5 before enabling.");
                AddDataSeries(BarsPeriodType.Tick, 1);
                Slippage = SlippagePerSide;
            }
            else if (State == State.DataLoaded)
            {
                if (Instrument.MasterInstrument.Name != "MNQ" || Math.Abs(TickSize - 0.25) > 1e-8
                    || Math.Abs(Instrument.MasterInstrument.PointValue - 2.0) > 1e-8)
                    throw new InvalidOperationException("Only MNQ ($2 point / $0.50 tick) is supported.");
                easternZone = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time");
                platformZone = TimeZoneInfo.FindSystemTimeZoneById(PlatformTimeZoneId);
                calendar = LoadCalendar(CalendarFile);
                candles = new Dictionary<DateTime, List<Candle>>();
                extendedCandles = new Dictionary<DateTime, List<Candle>>();
                invalidDays = new HashSet<DateTime>();
                ranges = new Queue<double>();
                executionIds = new HashSet<string>();
                results = new List<ResultSample>();
                string folder = Path.Combine(NinjaTrader.Core.Globals.UserDataDir, "MNQPaper", Safe(PaperRunId));
                Directory.CreateDirectory(folder);
                string label = Safe((Account == null ? "Analyzer" : Account.Name) + "_" + Instrument.FullName + "_" + Arm);
                ledgerPath = Path.Combine(folder, label + "_events.csv");
                tickPath = Path.Combine(folder, label + "_ticks.csv");
                reviewPath = Path.Combine(folder, label + "_loss_reviews.csv");
                riskLedgerPath = Path.Combine(NinjaTrader.Core.Globals.UserDataDir,"MNQPaper","risk-history",
                    Safe((Account==null?"Analyzer":Account.Name)+"_"+Instrument.FullName)+".csv");
                if (!File.Exists(ledgerPath))
                    File.WriteAllText(ledgerPath, "sequence,timestamp,account,stage,arm,reason,details\n");
                if (!File.Exists(tickPath))
                    File.WriteAllText(tickPath, "timestamp,price,volume,contract,tick_id,bid,ask\n");
                else if(new FileInfo(tickPath).Length>80)
                    throw new InvalidOperationException("This run already has tick data. Reconcile positions, then choose a new Paper run ID. Existing logs are preserved.");
                if (!File.Exists(reviewPath))
                    File.WriteAllText(reviewPath, "trade_id,timestamp,account,stage,arm,question,answer,status\n");
                tickWriter=new StreamWriter(new FileStream(tickPath,FileMode.Append,FileAccess.Write,FileShare.ReadWrite));
                Log(NowEastern(), "CONFIGURATION", "calendar_sha256=" + calendarHash
                    + ";stage=" + AccountStage + ";costs_calibrated=" + CostsCalibrated
                    + ";loss_limit=" + F(SessionLossLimit) + ";break_even=" + UseBreakEven
                    + ";thresholds=" + F(LongThreshold) + "/" + F(ShortThreshold)
                    + ";arm="+Arm+";trade_count_cap="+(IsRolling?"none":"1")
                    + ";rolling_momentum="+F(RollingMomentumThreshold)+";base_efficiency="+F(RollingMinEfficiency)+";adaptive="+AdaptiveQuality
                    +";overnight_efficiency=0.55;overnight_stop_atr=0.10;overnight_target_r=1.25;overnight_hold_minutes=20;R2_max_spread_ticks=2");
            }
            else if (State == State.Realtime)
            {
                if (!SimulationAccount())
                    throw new InvalidOperationException("Paper-only: attach to Sim101 or Playback101, never a funded brokerage account.");
                // Historical→Realtime never carries historical strategy positions into paper orders.
                if (AllowHistoricalOrders)
                    throw new InvalidOperationException("Turn off AllowHistoricalOrders before forward simulation.");
                Log(NowEastern(),"REALTIME_STARTED","paper_run_id="+PaperRunId+";log_folder="+Path.GetDirectoryName(ledgerPath));
                AcquireOwnership();
                if(!ownershipDenied)
                {
                    LoadResults();
                    DateTime resume=Account.Name=="Sim101"?NowEastern():lastTickTime;
                    dayRealized=results.Where(r=>TradingDay(r.ClosedAt)==day && r.ClosedAt<resume).Sum(r=>r.Net);
                    if(dayRealized<=-SessionLossLimit || AccountStage==MNQPaperStage.Evaluation && dayRealized>=EvaluationProfitTarget) blocked=true;
                    // Historical signals are discarded; require a fresh bar after resuming.
                    if(IsRolling) { signalFrozen=false; attempted=true; lastExitTime=resume; }
                    Log(resume,"RISK_RESTORED","day_net_usd="+F(dayRealized)+";observations="+results.Count);
                    if(IsRolling) foreach(bool night in Arm==MNQPaperArm.R2?new bool[] { false,true }:new bool[] { false })
                    foreach(int d in new int[] { 1,-1 })
                    {
                        QualityState q=QualityFor(d,resume,night);
                        Log(resume,"ADAPTATION_UPDATE","direction="+d+";observations="+q.Count+";recent_net_usd="+F(q.Net)
                            +";min_efficiency="+F(q.Minimum)+";tightened="+q.Tightened+";regime="+(night?"OVERNIGHT":"RTH")+";rule=restored_completed_history");
                    }
                }
                CheckStartup();
                if(Account.Name=="Sim101") heartbeat=new System.Threading.Timer(o=>
                {
                    if(State!=State.Realtime) return;
                    try { TriggerCustomEvent(x=>
                    {
                        DateTime wall=NowEastern();
                        if(entryOrder!=null && openQuantity==0 && wall>=entryDeadline
                            && (entryOrder.OrderState==OrderState.Working || entryOrder.OrderState==OrderState.Accepted))
                        { CancelOrder(entryOrder); Log(wall,"MISSED","CAPPED_ENTRY_EXPIRED"); }
                        if(session!=null && TradingDay(wall)==day && MarketOpen(session,wall)
                            && lastTickTime!=DateTime.MinValue && (wall-lastTickTime).TotalSeconds>MaxTickGapSeconds && !staleFaultRaised)
                        { staleFaultRaised=true; Fault(wall,"STALE_FEED","wall_et="+Iso(wall)
                            +";last_tick_et="+Iso(lastTickTime)+";tick_age_seconds="+F((wall-lastTickTime).TotalSeconds)
                            +";max_tick_gap_seconds="+MaxTickGapSeconds); }
                        if(session!=null && MarketOpen(session,wall) && wall>=FlattenAt(wall) && openQuantity>0 && !disconnected)
                            RequestExit(wall,ScheduledExitReason(wall));
                    },null); }
                    catch(InvalidOperationException) { /* Strategy terminated during the timer callback. */ }
                },null,1000,1000);
            }
            else if (State == State.Terminated && ledgerPath != null)
            {
                if(heartbeat!=null) { heartbeat.Dispose(); heartbeat=null; }
                if(tickWriter!=null) { tickWriter.Flush(); tickWriter.Dispose(); tickWriter=null; }
                if(ownerLease!=null) { ownerLease.Dispose(); ownerLease=null; }
                Log(NowEastern(), "TERMINATED", "Inspect account orders/positions; do not assume a stopped process protects positions.");
            }
        }

        private bool SimulationAccount()
        {
            return Account != null && (Account.Name == "Sim101" || Account.Name == "Playback101");
        }
        private bool OtherAccountPosition()
        {
            if (Account == null) return false;
            lock (Account.Positions)
                return Account.Positions.Any(p => p.Instrument.FullName == Instrument.FullName
                    && p.MarketPosition != MarketPosition.Flat && openQuantity == 0);
        }
        private void CheckStartup()
        {
            if (startupChecked) return;
            startupChecked = true;
            if (OtherAccountPosition())
            {
                blocked = true;
                Log(NowEastern(), "ORPHAN_OR_EXTERNAL_POSITION", "Entries blocked; reconcile ownership in Control Center. No external position is flattened.");
            }
        }
        private DateTime Eastern(DateTime ts)
        {
            return TimeZoneInfo.ConvertTime(DateTime.SpecifyKind(ts, DateTimeKind.Unspecified), platformZone, easternZone);
        }
        private string Iso(DateTime et)
        {
            return new DateTimeOffset(DateTime.SpecifyKind(et, DateTimeKind.Unspecified), easternZone.GetUtcOffset(et)).ToString("o", CultureInfo.InvariantCulture);
        }
        private DateTime NowEastern() { return TimeZoneInfo.ConvertTimeFromUtc(DateTime.UtcNow,easternZone); }
        private void LiveStatus(DateTime now,double price,double bid,double ask)
        {
            if(State!=State.Realtime || lastStatusTime!=DateTime.MinValue && (now-lastStatusTime).TotalSeconds<5) return;
            lastStatusTime=now;
            if(tickWriter!=null) tickWriter.Flush();
            double mark=direction>0?(bid>0?bid:price):(ask>0?ask:price);
            double unrealized=openQuantity>0?direction*(mark-entryFill)*2-RoundTurnFees-SlippagePerSide*0.50:0;
            string phase=session==null?"NO_CALENDAR_SESSION"
                :!MarketOpen(session,now)?(Arm==MNQPaperArm.R2?"CME_CLOSED":"OUTSIDE_RTH")
                :openQuantity>0?"POSITION_OPEN":EntryInFlight()?"ENTRY_PENDING"
                :blocked?"BLOCKED":NewsPaused(now)?"NEWS_PAUSE"
                :Arm==MNQPaperArm.R2 && (bid<=0 || ask<=0)?"WAITING_QUOTES"
                :Arm==MNQPaperArm.R2 && now<session.GlobexOpen.AddMinutes(30)?"WAITING_R2_BARS"
                :Arm==MNQPaperArm.R2 && now>=LastEntryAt(now)?"PRE_CLOSE"
                :Arm==MNQPaperArm.R1 && (now.TimeOfDay<new TimeSpan(10,0,0) || now.TimeOfDay>new TimeSpan(15,45,0))?"WAITING_R1_WINDOW":"SCANNING";
            DateTime received=NowEastern();
            Log(now,"LIVE_STATUS","price="+F(price)+";open_qty="+openQuantity+";unrealized_usd="+F(unrealized)
                +";day_net_usd="+F(dayRealized)+";blocked="+blocked+";entry_pending="+EntryInFlight()+";news_pause="+NewsPaused(now)+";phase="+phase
                +";received_at_et="+Iso(received)+";feed_age_seconds="+F((received-now).TotalSeconds)
                +";max_tick_gap_seconds="+MaxTickGapSeconds+";futures_day="+day.ToString("yyyy-MM-dd")
                +";regime="+(Arm==MNQPaperArm.R2 && Overnight(now)?"OVERNIGHT":"RTH"));
        }
        // Only these two explicit names for a quarterly MNQ expiry are aliases.
        // Preserve unknown identifiers and raw log/risk-history paths.
        private static string MnqContractKey(string value)
        {
            if(value==null) return value;
            string text=value.Trim().ToUpperInvariant();
            if(text.Length!=9 || !text.StartsWith("MNQ ",StringComparison.Ordinal)
                || text[7]<'0' || text[7]>'9' || text[8]<'0' || text[8]>'9') return value;
            string month=null;
            if(text[6]=='-')
            {
                string numeric=text.Substring(4,2);
                if(numeric=="03" || numeric=="06" || numeric=="09" || numeric=="12") month=numeric;
            }
            else switch(text.Substring(4,3))
            {
                case "MAR": month="03"; break;
                case "JUN": month="06"; break;
                case "SEP": month="09"; break;
                case "DEC": month="12"; break;
            }
            return month==null?value:"MNQ "+month+"-"+text.Substring(7,2);
        }
        private static bool SameMnqContract(string left,string right)
        {
            return String.Equals(MnqContractKey(left),MnqContractKey(right),StringComparison.Ordinal);
        }
        private static string F(double value) { return value.ToString("R", CultureInfo.InvariantCulture); }
        private static string Safe(string value)
        {
            return new string(value.Select(c => char.IsLetterOrDigit(c) || c == '-' ? c : '_').ToArray());
        }
        private static string Csv(string value) { return "\"" + (value ?? "").Replace("\"", "\"\"") + "\""; }
        private void Log(DateTime et, string reason, string details)
        {
            if (ledgerPath == null) return;
            string acct = Account == null ? "Analyzer" : Account.Name;
            File.AppendAllText(ledgerPath, (++eventSequence) + "," + Csv(Iso(et)) + "," + Csv(acct)
                + "," + AccountStage + "," + Arm + "," + reason + "," + Csv(details) + "\n");
            Print(reason + ": " + details);
        }
        private Dictionary<DateTime, SessionRule> LoadCalendar(string path)
        {
            if (!File.Exists(path)) throw new InvalidOperationException("Frozen calendar missing: " + path);
            string[] lines = File.ReadAllLines(path);
            if (lines.Length < 3 || !lines[0].StartsWith("# frozen_sha256=") || lines[0].Contains("synthetic=true"))
                throw new InvalidOperationException("Use export-nt-calendar with a real reviewed session/news/roll calendar.");
            using (SHA256 sha = SHA256.Create())
                calendarHash = BitConverter.ToString(sha.ComputeHash(File.ReadAllBytes(path))).Replace("-", "").ToLowerInvariant();
            const string legacyHeader = "date,open_et,close_et,contract,roll_day,late_news,news_flags";
            bool hasGlobex=lines[1]==legacyHeader+",trade_enabled,news_windows,globex_open_et,globex_close_et,globex_news_windows,globex_breaks";
            bool hasNewsWindows=hasGlobex || lines[1]==legacyHeader+",trade_enabled,news_windows";
            bool hasTradeEnabled = hasNewsWindows || lines[1] == legacyHeader + ",trade_enabled";
            if (!hasTradeEnabled && lines[1] != legacyHeader)
                throw new InvalidOperationException("Unexpected calendar header.");
            if(IsRolling && !hasNewsWindows)
                throw new InvalidOperationException("R1 needs an updated calendar CSV including scheduled news_windows. Reinstall the new package calendar.");
            if(Arm==MNQPaperArm.R2 && !hasGlobex)
                throw new InvalidOperationException("R2 needs the reviewed full-session calendar with Globex open/close/news/break columns. Install START-SIM101 from the R2 package.");
            var result = new Dictionary<DateTime, SessionRule>();
            foreach (string line in lines.Skip(2))
            {
                if (String.IsNullOrWhiteSpace(line)) continue;
                // Contract identifiers and flags must not contain comma/quote characters.
                string[] parts = line.Split(',');
                if (parts.Length != (hasGlobex?13:hasNewsWindows?9:hasTradeEnabled?8:7)) throw new InvalidOperationException("Invalid calendar row: " + line);
                var rule = new SessionRule { Day = DateTime.ParseExact(parts[0], "yyyy-MM-dd", CultureInfo.InvariantCulture),
                    Open = TimeSpan.Parse(parts[1], CultureInfo.InvariantCulture), Close = TimeSpan.Parse(parts[2], CultureInfo.InvariantCulture),
                    Contract = parts[3], Roll = Boolean.Parse(parts[4]), LateNews = Boolean.Parse(parts[5]), NewsFlags = parts[6],
                    TradeEnabled = !hasTradeEnabled || Boolean.Parse(parts[7]) };
                if (rule.Day.DayOfWeek == DayOfWeek.Saturday || rule.Day.DayOfWeek == DayOfWeek.Sunday
                    || rule.Open != new TimeSpan(9,30,0) || rule.Close <= rule.Open || rule.Close > new TimeSpan(16,0,0))
                    throw new InvalidOperationException("Invalid calendar date/session.");
                if(hasNewsWindows && parts[8]!="") foreach(string span in parts[8].Split('|'))
                {
                    string[] ends=span.Split('-');
                    if(ends.Length!=2) throw new InvalidOperationException("Invalid news window.");
                    TimeSpan start=TimeSpan.Parse(ends[0],CultureInfo.InvariantCulture),end=TimeSpan.Parse(ends[1],CultureInfo.InvariantCulture);
                    if(start<rule.Open || end>rule.Close || start>=end) throw new InvalidOperationException("News window outside RTH.");
                    rule.NewsWindows.Add(Tuple.Create(start,end));
                }
                if(hasGlobex && parts[9]!="" && parts[10]!="")
                {
                    rule.GlobexOpen=CalendarTime(parts[9]); rule.GlobexClose=CalendarTime(parts[10]);
                    if(rule.GlobexOpen!=rule.Day.AddDays(-1).AddHours(18) || rule.GlobexClose.Date!=rule.Day
                        || rule.GlobexClose.TimeOfDay>new TimeSpan(17,0,0) || rule.GlobexClose<rule.Day+rule.Close)
                        throw new InvalidOperationException("Invalid reviewed Globex session.");
                    rule.GlobexNews=DateWindows(parts[11],rule.GlobexOpen,rule.GlobexClose);
                    rule.GlobexBreaks=DateWindows(parts[12],rule.GlobexOpen,rule.GlobexClose);
                }
                else if(Arm==MNQPaperArm.R2) throw new InvalidOperationException("Missing reviewed Globex session for "+rule.Day.ToString("yyyy-MM-dd"));
                result.Add(rule.Day, rule);
            }
            return result;
        }
        private DateTime CalendarTime(string value)
        {
            DateTimeOffset ts=DateTimeOffset.Parse(value,CultureInfo.InvariantCulture);
            if(ts.Offset!=easternZone.GetUtcOffset(ts.DateTime)) throw new InvalidOperationException("Calendar must use Eastern civil timestamps with the correct UTC offset.");
            return ts.DateTime;
        }
        private List<Tuple<DateTime,DateTime>> DateWindows(string value,DateTime open,DateTime close)
        {
            var windows=new List<Tuple<DateTime,DateTime>>();
            if(value!="") foreach(string span in value.Split('|'))
            {
                string[] ends=span.Split('~');
                if(ends.Length!=2) throw new InvalidOperationException("Invalid full-session window.");
                DateTime a=CalendarTime(ends[0]),b=CalendarTime(ends[1]);
                if(a<open || b>close || a>=b) throw new InvalidOperationException("Full-session window outside reviewed market hours.");
                windows.Add(Tuple.Create(a,b));
            }
            return windows;
        }

        private void BeginDay(DateTime et)
        {
            if (openQuantity > 0) { Fault(et, "UNRESOLVED_POSITION"); return; }
            if (day != DateTime.MinValue && candles.ContainsKey(day) && calendar.ContainsKey(day)
                && candles[day].Count < (int)(calendar[day].Close-calendar[day].Open).TotalMinutes/5)
                { ranges.Clear(); previousClose=0; }
            day = TradingDay(et);
            session = calendar.ContainsKey(day) ? calendar[day] : null;
            attempted = signalFrozen = gapChosen = breakEvenArmed = exitPending = false;
            direction = 0; dayRealized = results.Where(r=>TradingDay(r.ClosedAt)==day && r.ClosedAt<et).Sum(r=>r.Net); lastTickTime = DateTime.MinValue;
            lastSignalEnd = DateTime.MinValue;
            staleFaultRaised=false;
            stopOrder = null; exitReason = "";
            atr = ranges.Count == 20 ? ranges.Average() : 0;
            blocked = session == null || disconnected || ownershipDenied;
            string reason = "ELIGIBLE";
            if (session == null) reason = "UNRESOLVED_SESSION";
            else if (session.Close != new TimeSpan(16,0,0)) { reason = "EARLY_CLOSE"; blocked = true; }
            else if (session.Roll) { reason = "ROLL_DAY"; blocked = true; }
            else if (!SameMnqContract(session.Contract,Instrument.FullName)) { reason = "UNRESOLVED_CONTRACT"; blocked = true; }
            else if (session.LateNews && !IsRolling) { reason = "NEWS_WINDOW"; blocked = true; }
            else if (!session.TradeEnabled) { reason = "WARMUP_ONLY"; blocked = true; }
            if (atr <= 0) blocked = true;
            if(dayRealized<=-SessionLossLimit || AccountStage==MNQPaperStage.Evaluation && dayRealized>=EvaluationProfitTarget) blocked=true;
            if (State == State.Realtime && OtherAccountPosition()) blocked = true;
            Log(et, reason, "atr20=" + F(atr) + ";contract=" + Instrument.FullName
                +";contract_key="+MnqContractKey(Instrument.FullName)+";calendar_contract="+(session==null?"":session.Contract)
                +";completed_atr_sessions="+ranges.Count);
            if (atr <= 0) Log(et, "ATR_WARMUP", "Load 21+ completed RTH sessions with matching tick history/calendar.");
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBars[0] < 0 || CurrentBars[1] < 0) return;
            if (BarsInProgress == 0)
            {
                DateTime end = Eastern(Times[0][0]);
                SessionRule rule;
                if(Arm==MNQPaperArm.R2)
                {
                    DateTime key=TradingDay(end.AddTicks(-1)); SessionRule full;
                    if(calendar.TryGetValue(key,out full) && MarketOpen(full,end.AddMinutes(-5)) && MarketOpen(full,end.AddTicks(-1)))
                    {
                        List<Candle> all;
                        if(!extendedCandles.TryGetValue(key,out all)) { all=new List<Candle>(); extendedCandles[key]=all; }
                        if(all.Count>0 && all[all.Count-1].End>=end) { Fault(end,"DUPLICATE_BAR"); return; }
                        all.Add(new Candle { End=end,Open=Opens[0][0],High=Highs[0][0],Low=Lows[0][0],Close=Closes[0][0] });
                    }
                }
                if (!calendar.TryGetValue(end.Date, out rule) || end.TimeOfDay <= rule.Open || end.TimeOfDay > rule.Close) return;
                List<Candle> list;
                if (!candles.TryGetValue(end.Date, out list)) { list = new List<Candle>(); candles[end.Date] = list; }
                if (list.Count > 0 && list[list.Count-1].End >= end) { Fault(end,"DUPLICATE_BAR"); return; }
                list.Add(new Candle { End=end, Open=Opens[0][0], High=Highs[0][0], Low=Lows[0][0], Close=Closes[0][0] });
                if (end.TimeOfDay == rule.Close)
                {
                    int expectedBars=(int)(rule.Close-rule.Open).TotalMinutes/5;
                    if (list.Count != expectedBars || invalidDays.Contains(end.Date) || !SameMnqContract(rule.Contract,Instrument.FullName))
                    { ranges.Clear(); previousClose=0; Log(end,"DATA_GAP","Incomplete/mismapped RTH; restart ATR warmup."
                        +";bars="+list.Count+";expected_bars="+expectedBars+";invalid_day="+invalidDays.Contains(end.Date)
                        +";contract="+Instrument.FullName+";calendar_contract="+rule.Contract); }
                    else
                    {
                        double high=list.Max(b=>b.High), low=list.Min(b=>b.Low), tr=high-low;
                        if (previousClose > 0 && SameMnqContract(previousContract,rule.Contract))
                            tr = Math.Max(tr, Math.Max(Math.Abs(high-previousClose), Math.Abs(low-previousClose)));
                        ranges.Enqueue(tr); if (ranges.Count > 20) ranges.Dequeue();
                        previousClose=list.Last().Close; previousContract=rule.Contract;
                    }
                }
                return;
            }
            if (BarsInProgress != 1) return;
            DateTime now = Eastern(Times[1][0]);
            if (TradingDay(now) != day) BeginDay(now);
            if (!MarketOpen(session,now))
            {
                if (openQuantity > 0 && session != null && now.TimeOfDay >= session.Close) Fault(now,"DATA_GAP");
                double outsidePrice=Closes[1][0],outsideBid=State==State.Realtime?GetCurrentBid():0,outsideAsk=State==State.Realtime?GetCurrentAsk():0;
                if(outsidePrice>0 && Volumes[1][0]>0 && ((outsideBid>0)==(outsideAsk>0)) && (outsideBid<=0 || outsideBid<=outsideAsk))
                    LiveStatus(now,outsidePrice,outsideBid,outsideAsk);
                return;
            }
            double price = Closes[1][0];
            double bid = State == State.Realtime ? GetCurrentBid() : 0;
            double ask = State == State.Realtime ? GetCurrentAsk() : 0;
            if(State==State.Realtime && Arm==MNQPaperArm.R2 && !marketFeedObserved && openQuantity==0 && !EntryInFlight()
                && price>0 && Volumes[1][0]>0 && (bid<=0 || ask<=0))
            {
                if(!quoteWaitLogged) { quoteWaitLogged=true; Log(now,"QUOTE_WAIT","Waiting for initial paired live bid/ask before R2 can enter;bid="+F(bid)+";ask="+F(ask)); }
                previousPrice=price; lastTickTime=now; LiveStatus(now,price,bid,ask); return;
            }
            if (Volumes[1][0] <= 0 || price <= 0 || ((bid>0)!=(ask>0)) || (bid > 0 && ask > 0 && bid > ask))
            { Fault(now,"DATA_QUALITY","price="+F(price)+";volume="+F(Volumes[1][0])+";bid="+F(bid)+";ask="+F(ask)); return; }
            if(State==State.Realtime && Account.Name=="Sim101")
            {
                DateTime wall=NowEastern(); double age=(wall-now).TotalSeconds;
                if(age>MaxTickGapSeconds && !staleFaultRaised)
                { staleFaultRaised=true; Fault(wall,"STALE_FEED","last_tick_et="+Iso(now)+";tick_age_seconds="+F(age)+";max_tick_gap_seconds="+MaxTickGapSeconds); }
                if(!marketFeedObserved && age>=-5 && age<=MaxTickGapSeconds && bid>0 && ask>0)
                { marketFeedObserved=true; disconnected=false; Log(wall,"FEED_READY","Initial fresh live quote observed; existing calendar/history/risk blocks are retained."); }
            }
            // R2 warms from NinjaTrader's retained history without duplicating weeks
            // of full-session ticks into OneDrive. Export the forward path and any
            // explicitly enabled Analyzer order run.
            if(Arm!=MNQPaperArm.R2 || State!=State.Historical || AllowHistoricalOrders)
            {
                tickWriter.WriteLine(Iso(now)+","+F(price)+","+F(Volumes[1][0])+","+Csv(Instrument.FullName)
                    +",nt-"+Safe(PaperRunId)+"-"+(++tickSequence)+","+(bid>0?F(bid):"")+","+(ask>0?F(ask):""));
                if(tickSequence%1000==0) tickWriter.Flush();
            }
            if (lastTickTime != DateTime.MinValue && now < lastTickTime) { Fault(now,"BAD_TIMESTAMP"); return; }
            DateTime opening=Arm==MNQPaperArm.R2?session.GlobexOpen:day+session.Open;
            if ((lastTickTime == DateTime.MinValue && (now-opening).TotalSeconds > MaxTickGapSeconds)
                || (lastTickTime != DateTime.MinValue && GapSeconds(lastTickTime,now) > MaxTickGapSeconds)) Fault(now,"DATA_GAP");
            double before = previousPrice;
            previousPrice = price; lastTickTime = now;
            LiveStatus(now,price,bid,ask);
            if(entryOrder!=null && openQuantity==0 && now>=entryDeadline
                && (entryOrder.OrderState==OrderState.Working || entryOrder.OrderState==OrderState.Accepted))
            { CancelOrder(entryOrder); Log(now,"MISSED","CAPPED_ENTRY_EXPIRED"); }
            if (disconnected) { if(openQuantity>0) RequestExit(now,"FAULT_EXIT"); return; }
            EvaluateSignal(now);
            if (openQuantity > 0)
            {
                if (exitPending) return;
                double executable = direction > 0 ? (bid>0?bid:price) : (ask>0?ask:price);
                // A native working stop is already active; if crossing occurs, do not race a market exit.
                if (direction*(executable-stopPrice) <= 0) return;
                double tradeNet=direction*(executable-entryFill)*2-RoundTurnFees-SlippagePerSide*0.50;
                double estimatedNet = dayRealized + tradeNet;
                mfe=Math.Max(mfe,tradeNet); mae=Math.Min(mae,tradeNet); tradeTicks++;
                if (now >= FlattenAt(now)) RequestExit(now,ScheduledExitReason(now));
                else if(IsRolling && NewsPaused(now)) RequestExit(now,"NEWS_EXIT");
                else if (estimatedNet <= -SessionLossLimit) RequestExit(now,"LOSS_BUDGET_EXIT");
                else if (AccountStage==MNQPaperStage.Evaluation && estimatedNet >= EvaluationProfitTarget) RequestExit(now,"DAILY_PROFIT_TARGET");
                else if(IsRolling && now>=entryTime.AddMinutes(Arm==MNQPaperArm.R2 && entryOvernight?20:30)) RequestExit(now,"HOLD_EXIT");
                else if (UseBreakEven && !breakEvenArmed && direction*(executable-entryFill) >= BreakEvenTriggerR*initialRisk)
                {
                    double cover=Math.Ceiling(RoundTurnFees/0.50+SlippagePerSide)*TickSize;
                    double proposed=Outward(entryFill+direction*cover,-direction);
                    if(direction*(executable-proposed)>TickSize)
                    {
                        stopPrice=proposed; breakEvenArmed=true; PlaceStop();
                        Log(now,"BREAK_EVEN_STOP","price="+F(stopPrice)+";guaranteed=false");
                    }
                }
                return;
            }
            if (blocked || attempted || !signalFrozen || direction==0 || EntryInFlight()) return;
            if(IsRolling && NewsPaused(now)) { attempted=true; Log(now,"NEWS_PAUSE","Fresh entries paused around a scheduled release."); return; }
            if(dayRealized<=-SessionLossLimit || AccountStage==MNQPaperStage.Evaluation && dayRealized>=EvaluationProfitTarget)
            { blocked=true; Log(now,"DAILY_RISK_LIMIT","day_net_usd="+F(dayRealized)); return; }
            if (State == State.Historical && !AllowHistoricalOrders) return;
            if (State == State.Realtime && !SimulationAccount()) { Fault(now,"NOT_SIMULATION_ACCOUNT"); return; }
            bool trigger = Arm==MNQPaperArm.P0
                ? now.TimeOfDay >= new TimeSpan(15,30,0) && now.TimeOfDay < new TimeSpan(15,59,0)
                : IsRolling ? now>=gapFormed && now<gapExpires && now<FlattenAt(now)
                : now >= gapFormed && now < gapExpires && (direction>0?before>=gapMidpoint && price<gapMidpoint:before<=gapMidpoint && price>gapMidpoint);
            if (Arm==MNQPaperArm.C1 && now>=gapExpires) { attempted=true; Log(now,"MISSED","SETUP_EXPIRED"); return; }
            if (!trigger) return;
            attempted = true;
            if(Arm==MNQPaperArm.R2 && (bid<=0 || ask<=0 || (ask-bid)/TickSize>2))
            { Log(now,"REJECTED","R2_QUOTE_OR_SPREAD_FILTER"); return; }
            double market = direction>0 ? (ask>0?ask:price+Math.Ceiling(SpreadTicks/2.0)*TickSize)
                : (bid>0?bid:price-Math.Floor(SpreadTicks/2.0)*TickSize);
            double expected = market+direction*SlippagePerSide*TickSize;
            double reference = Arm==MNQPaperArm.P0?before:IsRolling?rollingReference:gapMidpoint;
            if (Math.Max(0,direction*(expected-reference)/TickSize)>EntrySlippageCap) { Log(now,"MISSED","SLIPPAGE_CAP"); return; }
            double risk = Arm==MNQPaperArm.P0 ? Math.Ceiling(0.20*atr/TickSize)*TickSize : direction*(expected-gapStop);
            double cost=Math.Ceiling(RoundTurnFees/0.50+(bid>0&&ask>0?(ask-bid)/TickSize:SpreadTicks)+2*SlippagePerSide);
            if (risk<=0 || Arm!=MNQPaperArm.P0 && risk/TickSize<2*cost || IsRolling && risk>MaximumStopAtr()*atr
                || risk*2+RoundTurnFees+SlippagePerSide*0.50>SessionLossLimit+Math.Min(0,dayRealized) || UseBreakEven && BreakEvenTriggerR*risk/TickSize<=cost)
            { if(!IsRolling) blocked=true; Log(now,"REJECTED","INITIAL_RISK_OR_LOSS_BUDGET"); return; }
            if(State==State.Realtime && OtherAccountPosition()) { blocked=true; Log(now,"ORPHAN_OR_EXTERNAL_POSITION","Entries blocked."); return; }
            if(State==State.Realtime && !ClaimSession(now)) { if(!IsRolling) blocked=true; return; }
            submittedReference=reference;
            entryDeadline=now.AddSeconds(5);
            tickWriter.Flush();
            Log(now,"SUBMITTED","quantity=1;bid="+F(bid)+";ask="+F(ask));
            // A marketable capped limit enforces the chase cap at the actual simulated fill.
            double limit=Outward(reference+direction*EntrySlippageCap*TickSize,direction);
            entrySubmissionPending=true;
            if(direction>0) EnterLongLimit(1,true,1,limit,"MNQ_ENTRY");
            else EnterShortLimit(1,true,1,limit,"MNQ_ENTRY");
        }

        private void EvaluateSignal(DateTime now)
        {
            if (blocked || atr<=0) return;
            List<Candle> list;
            if (!(Arm==MNQPaperArm.R2?extendedCandles:candles).TryGetValue(day,out list)) return;
            if(IsRolling)
            {
                if(list.Count==0) return;
                DateTime end=list[list.Count-1].End;
                if(openQuantity>0 || EntryInFlight()) { lastSignalEnd=end; return; }
                if(end<=lastSignalEnd) return;
                lastSignalEnd=end; signalFrozen=false; attempted=false; direction=0;
                if(list.Count<6 || end<=lastExitTime || (Arm==MNQPaperArm.R2
                    ?end<session.GlobexOpen.AddMinutes(30) || end>LastEntryAt(end)
                    :end.TimeOfDay<new TimeSpan(10,0,0) || end.TimeOfDay>new TimeSpan(15,45,0))) return;
                entryOvernight=Arm==MNQPaperArm.R2 && Overnight(end);
                var window=list.Skip(list.Count-6).ToList();
                for(int i=1;i<window.Count;i++) if(window[i-1].End.AddMinutes(5)!=window[i].End) return;
                Candle a=window[3], b=window[4], c=window[5];
                double change=c.Close-window[0].Open, m=change/atr;
                int d=m>=RollingMomentumThreshold?1:m<=-RollingMomentumThreshold?-1:0;
                double path=Math.Abs(window[0].Close-window[0].Open);
                for(int i=1;i<window.Count;i++) path+=Math.Abs(window[i].Close-window[i-1].Close);
                double efficiency=path>0?Math.Abs(change)/path:0, width=c.High-c.Low;
                QualityState quality=QualityFor(d,end);
                string reject=d==0?"WEAK_MOMENTUM":efficiency<quality.Minimum?"QUALITY_FILTER":width<=0 || width>0.25*atr?"BAR_RANGE":
                    d*(b.Close-b.Open)>=0?"NO_PULLBACK":d>0?(c.Close<=Math.Max(a.High,b.High) || (c.Close-c.Low)/width<0.70?"NO_BREAKOUT":""):
                    (c.Close>=Math.Min(a.Low,b.Low) || (c.High-c.Close)/width<0.70?"NO_BREAKOUT":"");
                double stop=Outward(d>0?b.Low-TickSize:b.High+TickSize,d), risk=d*(c.Close-stop);
                if(reject=="" && (risk<=0 || risk>MaximumStopAtr()*atr)) reject="STOP_DISTANCE";
                Log(end,"SCAN_RESULT","decision="+(reject==""?"SETUP":reject)+";efficiency="+F(efficiency)
                    +";min_efficiency="+F(quality.Minimum)+";observations="+quality.Count+";momentum="+F(m));
                if(reject!="") return;
                direction=d; signalM=m; rollingReference=c.Close; gapStop=stop; rollingEfficiency=efficiency; entryQualityMinimum=quality.Minimum;
                gapFormed=end; gapExpires=end.AddMinutes(5); signalFrozen=true;
                Log(end,d>0?"LONG":"SHORT","setup="+Arm+";formed="+Iso(end)+";reference="+F(c.Close)+";stop="+F(stop)
                    +";efficiency="+F(efficiency)+";min_efficiency="+F(quality.Minimum));
                return;
            }
            if(signalFrozen) return;
            if (Arm==MNQPaperArm.P0)
            {
                if (now.TimeOfDay<new TimeSpan(10,0,0)) return;
                var six=list.Where(b=>b.End.TimeOfDay<=new TimeSpan(10,0,0)).ToList();
                if(six.Count<6) return; // Multi-series dispatch may publish the closed bar after this tick.
                signalFrozen=true;
                if(six.Count!=6 || six[0].End.TimeOfDay!=new TimeSpan(9,35,0) || six[5].End.TimeOfDay!=new TimeSpan(10,0,0)) { Fault(now,"DATA_GAP"); return; }
                double m=(six[5].Close-six[0].Open)/atr;
                signalM=m;
                direction=m>=LongThreshold?1:m<=-ShortThreshold?-1:0;
                Log(day.AddHours(10),direction>0?"LONG":direction<0?"SHORT":"BELOW_THRESHOLD","M="+F(m)+";ATR20="+F(atr));
            }
            else
            {
                if(list.Count<3 || gapChosen) return;
                for(int i=2;i<list.Count;i++)
                {
                    Candle a=list[i-2], b=list[i-1], c=list[i];
                    if(c.End<=lastSignalEnd) continue;
                    lastSignalEnd=c.End;
                    if(c.End.TimeOfDay>new TimeSpan(11,30,0) || a.End.AddMinutes(5)!=b.End || b.End.AddMinutes(5)!=c.End) continue;
                    int d=a.High<c.Low?1:a.Low>c.High?-1:0;
                    if(d==0) continue;
                    double lo=d>0?a.High:c.High, hi=d>0?c.Low:a.Low;
                    if(hi-lo<Math.Max(2*TickSize,0.01*atr) || b.High-b.Low<0.05*atr || d*(c.Close-list[0].Open)<=0) continue;
                    direction=d; gapMidpoint=(lo+hi)/2; gapStop=Outward(d>0?a.Low-TickSize:a.High+TickSize,d);
                    gapFormed=c.End; gapExpires=c.End.AddMinutes(30); gapChosen=signalFrozen=true;
                    Log(c.End,d>0?"LONG":"SHORT","FVG midpoint="+F(gapMidpoint)+";stop="+F(gapStop));
                    break;
                }
            }
        }
        private double Outward(double price,int d)
        {
            return (d>0?Math.Floor(price/TickSize+1e-9):Math.Ceiling(price/TickSize-1e-9))*TickSize;
        }
        private bool ClaimSession(DateTime now)
        {
            // R1 claims each distinct closed-bar setup; P0/C1 retain their baseline daily claim.
            string lockFolder=Path.Combine(NinjaTrader.Core.Globals.UserDataDir,"MNQPaper","entry-locks");
            Directory.CreateDirectory(lockFolder);
            string suffix=IsRolling?gapFormed.ToString("yyyyMMddHHmm")+"_"+Arm:day.ToString("yyyyMMdd");
            string path=Path.Combine(lockFolder,Safe(Account.Name+"_"+Instrument.FullName)+"_"+suffix+".entry");
            try { using(FileStream file=new FileStream(path,FileMode.CreateNew,FileAccess.Write))
                { byte[] bytes=Encoding.UTF8.GetBytes(Iso(now)); file.Write(bytes,0,bytes.Length); } return true; }
            catch(IOException) { Log(now,"REJECTED","SETUP_ALREADY_CLAIMED; preserved across run IDs"); return false; }
        }
        private bool EntryInFlight()
        {
            return entrySubmissionPending || entryOrder!=null && entryOrder.OrderState!=OrderState.Filled
                && entryOrder.OrderState!=OrderState.Cancelled && entryOrder.OrderState!=OrderState.Rejected;
        }
        private bool NewsPaused(DateTime now)
        {
            return session!=null && (Arm==MNQPaperArm.R2?session.GlobexNews.Any(w=>w.Item1<=now && now<w.Item2)
                :session.NewsWindows.Any(w=>w.Item1<=now.TimeOfDay && now.TimeOfDay<w.Item2));
        }
        private void AcquireOwnership()
        {
            string folder=Path.Combine(NinjaTrader.Core.Globals.UserDataDir,"MNQPaper","owners");
            Directory.CreateDirectory(folder);
            string path=Path.Combine(folder,Safe(Account.Name+"_"+Instrument.FullName)+".lock");
            try { ownerLease=new FileStream(path,FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None); }
            catch(IOException) { ownershipDenied=blocked=true; Log(NowEastern(),"STRATEGY_ALREADY_RUNNING","Another instance owns this account/instrument."); }
        }
        private void LoadResults()
        {
            results.Clear();
            Directory.CreateDirectory(Path.GetDirectoryName(riskLedgerPath));
            const string header="trade_id,exit_et,arm,direction,net_usd,quality_eligible";
            if(!File.Exists(riskLedgerPath)) { File.WriteAllText(riskLedgerPath,header+"\n"); return; }
            string[] lines=File.ReadAllLines(riskLedgerPath);
            if(lines.Length==0 || lines[0]!=header) throw new InvalidOperationException("Invalid persisted risk history; reconcile before enabling.");
            var ids=new HashSet<string>();
            foreach(string line in lines.Skip(1))
            {
                if(String.IsNullOrWhiteSpace(line)) continue;
                string[] p=line.Split(',');
                if(p.Length!=6 || !ids.Add(p[0])) throw new InvalidOperationException("Invalid or duplicate risk-history row.");
                DateTime closed=TimeZoneInfo.ConvertTime(DateTimeOffset.Parse(p[1],CultureInfo.InvariantCulture),easternZone).DateTime;
                int d=Int32.Parse(p[3],CultureInfo.InvariantCulture); double net=Double.Parse(p[4],CultureInfo.InvariantCulture);
                if((d!=1 && d!=-1) || Double.IsNaN(net) || Double.IsInfinity(net)) throw new InvalidOperationException("Invalid risk-history numeric value.");
                results.Add(new ResultSample { Id=p[0],ClosedAt=closed,Arm=p[2],Direction=d,Net=net,QualityEligible=Boolean.Parse(p[5]) });
            }
        }
        private QualityState QualityFor(int d,DateTime asOf,bool? night=null)
        {
            bool overnight=Arm==MNQPaperArm.R2 && (night.HasValue?night.Value:Overnight(asOf));
            string qualityArm=QualityArm(overnight);
            var recent=results.Where(r=>r.Arm==qualityArm && r.QualityEligible && r.Direction==d && r.ClosedAt<asOf)
                .OrderBy(r=>r.ClosedAt).ThenBy(r=>r.Id,StringComparer.Ordinal).Reverse().Take(8).ToList();
            double net=recent.Sum(r=>r.Net);
            bool tighten=AdaptiveQuality && recent.Count==8 && net<=0;
            double baseline=overnight?Math.Max(0.55,RollingMinEfficiency):RollingMinEfficiency;
            return new QualityState { Count=recent.Count,Net=net,Tightened=tighten,Minimum=Math.Min(0.90,baseline+(tighten?0.15:0)) };
        }
        private void RecordResult(DateTime now,double net,string reason)
        {
            if(State!=State.Realtime) return;
            if(ownerLease==null) { Fault(now,"RISK_HISTORY_NOT_OWNED"); return; }
            string id=Convert.ToBase64String(Encoding.UTF8.GetBytes(tradeKey));
            if(results.Any(r=>r.Id==id)) { Fault(now,"DUPLICATE_RESULT"); return; }
            bool eligible=!invalidDays.Contains(TradingDay(now)) && !disconnected && reason!="FAULT_EXIT";
            string sampleArm=Arm==MNQPaperArm.R2?QualityArm(entryOvernight):Arm.ToString();
            try { File.AppendAllText(riskLedgerPath,id+","+Iso(now)+","+sampleArm+","+direction+","+F(net)+","+eligible+"\n"); }
            catch(IOException) { Fault(now,"RISK_HISTORY_WRITE_FAILED"); return; }
            results.Add(new ResultSample { Id=id,ClosedAt=now,Arm=sampleArm,Direction=direction,Net=net,QualityEligible=eligible });
            if(IsRolling)
            {
                QualityState q=QualityFor(direction,now.AddTicks(1),entryOvernight);
                Log(now,"ADAPTATION_UPDATE","direction="+direction+";observations="+q.Count+";recent_net_usd="+F(q.Net)
                    +";min_efficiency="+F(q.Minimum)+";tightened="+q.Tightened+";regime="+(entryOvernight?"OVERNIGHT":"RTH")+";rule=eight_completed_trades");
            }
        }
        private void PlaceStop()
        {
            if(openQuantity<=0) return;
            if(direction>0) ExitLongStopMarket(1,true,openQuantity,stopPrice,"EMERGENCY_STOP","MNQ_ENTRY");
            else ExitShortStopMarket(1,true,openQuantity,stopPrice,"EMERGENCY_STOP","MNQ_ENTRY");
        }
        private void RequestExit(DateTime now,string reason)
        {
            if(!IsRolling || reason!="HOLD_EXIT" && reason!="NEWS_EXIT" && reason!="MARKET_BREAK_EXIT") blocked=true;
            if(openQuantity<=0 || exitPending) return;
            exitPending=true; exitReason=reason;
            Log(now,"SUBMITTED","exit="+reason+";quantity="+openQuantity);
            if(direction>0) ExitLong(1,openQuantity,reason,"MNQ_ENTRY");
            else ExitShort(1,openQuantity,reason,"MNQ_ENTRY");
        }
        private void Fault(DateTime now,string reason,string evidence=null)
        {
            blocked=true; invalidDays.Add(TradingDay(now)); Log(now,reason,"New entries blocked for session."
                +(String.IsNullOrEmpty(evidence)?"":";"+evidence));
            if(entryOrder!=null && openQuantity==0 && (entryOrder.OrderState==OrderState.Working || entryOrder.OrderState==OrderState.Accepted))
                CancelOrder(entryOrder);
            if(!disconnected && openQuantity>0) RequestExit(now,"FAULT_EXIT");
        }
        protected override void OnExecutionUpdate(Execution execution,string executionId,double price,int quantity,
            MarketPosition marketPosition,string orderId,DateTime time)
        {
            if(!executionIds.Add(executionId)) { Fault(Eastern(time),"DUPLICATE_EXECUTION"); return; }
            DateTime now=Eastern(time);
            Log(now,"FILLED","execution_id="+executionId+";order_id="+orderId+";name="+execution.Name+";quantity="+quantity+";price="+F(price)+";direction="+direction
                +";futures_day="+day.ToString("yyyy-MM-dd")+";regime="+(entryOvernight?"OVERNIGHT":"RTH"));
            if(execution.Name=="MNQ_ENTRY")
            {
                openQuantity+=quantity; entryFill=price;
                entryTime=now; breakEvenArmed=false; exitPending=false; exitReason="";
                tradeKey=(Account==null?"Analyzer":Account.Name)+":"+orderId;
                stopEverAccepted=false; tradeTicks=0; mfe=mae=-RoundTurnFees;
                initialRisk=Arm==MNQPaperArm.P0?Math.Ceiling(0.20*atr/TickSize)*TickSize:direction*(price-gapStop);
                stopPrice=Arm==MNQPaperArm.P0?Outward(price-direction*initialRisk,direction):gapStop;
                PlaceStop(); Log(now,"STOP_ACTIVE","price="+F(stopPrice)+";native_simulator_order=true");
                if(Arm!=MNQPaperArm.P0)
                {
                    double target=Outward(price+direction*(Arm==MNQPaperArm.R2 && entryOvernight?1.25:1.5)*initialRisk,-direction);
                    if(direction>0) ExitLongLimit(1,true,openQuantity,target,"TARGET_EXIT","MNQ_ENTRY");
                    else ExitShortLimit(1,true,openQuantity,target,"TARGET_EXIT","MNQ_ENTRY");
                }
                if(openQuantity!=1 || initialRisk<=0 || initialRisk*2+RoundTurnFees+SlippagePerSide*0.50>SessionLossLimit+Math.Min(0,dayRealized)
                    || IsRolling && initialRisk>MaximumStopAtr()*atr
                    || Arm!=MNQPaperArm.P0 && initialRisk/TickSize<2*Math.Ceiling(RoundTurnFees/0.50+SpreadTicks+2*SlippagePerSide))
                    Fault(now,"UNEXPECTED_FILL_OR_RISK");
                else if(direction*(price-submittedReference)/TickSize>EntrySlippageCap)
                    Fault(now,"FILL_EXCEEDED_SLIPPAGE_CAP");
            }
            else
            {
                double net=direction*(price-entryFill)*2*quantity-RoundTurnFees*quantity;
                dayRealized+=net; openQuantity-=quantity;
                string reason=execution.Name;
                if(reason=="FAULT_EXIT" || reason=="EMERGENCY_STOP" && !IsRolling) blocked=true;
                Log(now,reason,"net_usd="+F(net)+";day_net_usd="+F(dayRealized)+";fees="+F(RoundTurnFees*quantity));
                if(net<0) { WriteLossReview(now,reason,net,price); Log(now,"LOSS_RECORDED","trade_id="+tradeKey+";net_usd="+F(net)+";questions=30"); }
                RecordResult(now,net,reason);
                if(dayRealized<=-SessionLossLimit || AccountStage==MNQPaperStage.Evaluation && dayRealized>=EvaluationProfitTarget) blocked=true;
                if(openQuantity<=0)
                {
                    openQuantity=0; stopOrder=null; exitPending=false; entrySubmissionPending=false; entryOrder=null; lastExitTime=now;
                    if(IsRolling) { signalFrozen=false; attempted=true; gapChosen=false; }
                }
            }
        }
        protected override void OnOrderUpdate(Order order,double limitPrice,double stopPrice,int quantity,int filled,
            double averageFillPrice,OrderState orderState,DateTime time,ErrorCode error,string nativeError)
        {
            if(order.Name=="EMERGENCY_STOP") stopOrder=order;
            if(order.Name=="MNQ_ENTRY") entryOrder=order;
            if(order.Name=="MNQ_ENTRY" && (orderState==OrderState.Filled || orderState==OrderState.Cancelled || orderState==OrderState.Rejected)) entrySubmissionPending=false;
            if(order.Name=="EMERGENCY_STOP" && (orderState==OrderState.Accepted || orderState==OrderState.Working)) stopEverAccepted=true;
            if(order.Name!="MNQ_ENTRY" && orderState==OrderState.Filled) exitPending=true;
            string code=orderState==OrderState.PartFilled?"PARTIAL":orderState==OrderState.Rejected?"REJECTED":orderState==OrderState.Cancelled?"CANCELLED":"ORDER_UPDATE";
            Log(Eastern(time),code,"order_id="+order.OrderId+";name="+order.Name+";state="+orderState+";filled="+filled+";error="+error+";native="+nativeError);
            if(orderState==OrderState.PartFilled || orderState==OrderState.Rejected || error!=ErrorCode.NoError)
            { blocked=true; invalidDays.Add(TradingDay(Eastern(time))); } // StopCancelClose handles native rejection cleanup.
            if(order.Name=="EMERGENCY_STOP" && orderState==OrderState.Cancelled && openQuantity>0 && !exitPending)
                Fault(Eastern(time),"STOP_CANCELLED");
        }
        protected override void OnConnectionStatusUpdate(ConnectionStatusEventArgs update)
        {
            if(State!=State.Realtime) return;
            bool previouslyDisconnected=disconnected;
            disconnected=update.Status!=ConnectionStatus.Connected || update.PriceStatus!=ConnectionStatus.Connected;
            // Startup notifications cannot have interrupted an order before the first
            // fresh quote. A repeated Connected notification is not a reconnection.
            if(!ConnectionShouldLatch(marketFeedObserved,openQuantity>0 || EntryInFlight(),disconnected,previouslyDisconnected))
            {
                if(ledgerPath!=null) Log(NowEastern(),disconnected?"CONNECTION_WAIT":"CONNECTION_READY",
                    "Initial/status notification; no session fault latched;status="+update.Status+";price_status="+update.PriceStatus);
                return;
            }
            blocked=true;
            if(ledgerPath!=null) Log(NowEastern(),
                disconnected?"DISCONNECT":"RECONNECT","Entries remain blocked; working stop retained; next tradable tick flattens strategy position."
                +";connection_status="+update.Status+";price_status="+update.PriceStatus);
            if(!disconnected && openQuantity>0)
                TriggerCustomEvent(o=>RequestExit(lastTickTime,"FAULT_EXIT"),null);
        }

        private void WriteLossReview(DateTime now,string reason,double net,double exitPrice)
        {
            var rows=new List<string>();
            Action<string,string,string> q=(question,answer,status)=>rows.Add(Csv(tradeKey)+","+Csv(Iso(now))+","+
                Csv(Account==null?"Analyzer":Account.Name)+","+AccountStage+","+Arm+","+Csv(question)+","+Csv(answer)+","+status);
            q("Did the setup meet all mechanical criteria?","Configured entry guards passed; M="+F(signalM)+";ATR20="+F(atr),"info");
            q("Was the signal based on completed bars?",IsRolling?"Six closed rolling bars; setup="+Iso(gapFormed)+";efficiency="+F(rollingEfficiency)+";required="+F(entryQualityMinimum):"P0 uses closed opening bars; C1 uses closed three-bar formation.","info");
            q("Was ATR20 lagged one session?","Prior completed-session queue; ATR20="+F(atr),"info");
            q(Arm==MNQPaperArm.R2?"Was this a reviewed full futures session?":"Was this a full RTH session?",session==null?"Unknown":
                Arm==MNQPaperArm.R2?"Globex="+Iso(session.GlobexOpen)+" to "+Iso(session.GlobexClose)+";entry_profile="+(entryOvernight?"OVERNIGHT":"RTH"):session.Close.ToString(),session!=null&&session.Close==new TimeSpan(16,0,0)?"pass":"flag");
            q("Was prohibited late news excluded?",session==null?"Unknown":"late_news="+session.LateNews,"info");
            q("Was the contract map resolved and rollover excluded?",session==null?"Unknown":"contract="+session.Contract+";roll="+session.Roll,"info");
            q("Were there data or connection faults?","invalid_day="+invalidDays.Contains(day)+";disconnected="+disconnected,invalidDays.Contains(day)?"flag":"info");
            q("Was a stop resting on a broker server?","A native simulation stop was submitted; no external broker server is involved.","unknown");
            q("Was the simulation stop accepted or working?","accepted_or_working="+stopEverAccepted,stopEverAccepted?"pass":"unknown");
            q("Was a stop submitted immediately after the entry execution?","PlaceStop is called from the entry execution callback; inspect order timestamps.","info");
            q("Was quantity limited to one MNQ?","Strategy submits one contract; inspect execution quantity for reconciliation.","info");
            q("Did the bot add to a losing position?","No averaging or pyramiding entry path exists.","pass");
            q("Was entry beyond the slippage cap?","Cap="+EntrySlippageCap+";reference="+F(submittedReference)+";fill="+F(entryFill),"info");
            q("Were actual commissions and fees calibrated?","calibrated="+CostsCalibrated+";round_trip_fees="+F(RoundTurnFees),CostsCalibrated?"pass":"unknown");
            q("Was spread observed in the fill path?","Use captured bid/ask and native fill ledger; historical quotes may be absent.","unknown");
            q("Did fees erase a non-losing fill result?","before_fees_usd="+F(net+RoundTurnFees),net+RoundTurnFees>=0?"flag":"info");
            q("Was the pre-entry market trending or choppy?",IsRolling?"Six-bar trend efficiency="+F(rollingEfficiency)+";minimum="+F(entryQualityMinimum):"Requires raw-tick or 5-minute-bar analysis; no discretionary label is assumed.",IsRolling?"info":"unknown");
            q("How large was the initial risk relative to ATR?","risk="+F(initialRisk)+";risk_to_atr="+F(atr>0?initialRisk/atr:0),"info");
            q("What was maximum adverse excursion?","observed_mae_net_usd="+F(Math.Min(mae,net)),"info");
            q("Was the trade profitable before losing?","observed_mfe_net_usd="+F(mfe),mfe>0?"flag":"info");
            q("Was break-even protection armed?","enabled="+UseBreakEven+";armed="+breakEvenArmed+";trigger_R="+F(BreakEvenTriggerR),"info");
            q("Did a stop gap or slippage defeat protection?","exit_fill="+F(exitPrice)+";stop="+F(stopPrice)+";adverse_stop_ticks="+F(Math.Max(0,direction*(stopPrice-exitPrice)/TickSize)),"info");
            q("Which exit mechanism realized this loss?",reason,"info");
            q("Was the setup fresh rather than a duplicate entry?",IsRolling?"Each closed-bar setup has a persistent claim; multiple distinct setups are allowed.":"Baseline session entry claim retained.","info");
            q("Did cumulative realized loss exceed the session budget?","day_net_usd="+F(dayRealized)+";budget="+F(SessionLossLimit),-dayRealized>SessionLossLimit?"flag":"pass");
            q("Was the correct account stage selected?","stage="+AccountStage+";profit_target="+(AccountStage==MNQPaperStage.Evaluation?F(EvaluationProfitTarget):"none"),"info");
            q("Was the firm trailing-drawdown rule breached?","Firm limits and external account equity are not configured.","unknown");
            q("Did a manual override or another strategy interfere?","Use a dedicated Sim101/Playback101 run; external/manual order ownership is unverified.","unknown");
            q("Did latency, partial fill, reject or cancel contribute?","Reconcile native ORDER_UPDATE/PARTIAL/REJECTED/CANCELLED events and "+tradeTicks+" observed ticks.","unknown");
            q("Would a proposed fix generalize to future data?","Unknown; R1 logs bounded quality-filter changes using completed outcomes. Compare adaptive/fixed runs and future simulation results before claiming improvement.","unknown");
            File.AppendAllText(reviewPath,String.Join("\n",rows)+"\n");
        }
    }
}
