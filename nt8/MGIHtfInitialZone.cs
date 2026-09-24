#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Windows;
using System.Windows.Media;
using System.Xml.Serialization;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
	/// <summary>
	/// MGI HTF InitialZone : detecte les trend-candles Daily (|OC| > ATR Wilder 20)
	/// et Weekly (|OC| > ATR Wilder 12, standard NT8). L'initial candle = periode
	/// precedente. Initial zone = Value Area (VAH-VAL, 70% defaut) du volume 1-min
	/// NT8 de l'initial candle (rows de 4 ticks, volume reparti uniformement sur
	/// [Low..High] comme SwingUniformProfile). Swing level = extreme de la
	/// trendcandle (PB = Low si haussiere, PH = High si baissiere).
	/// Zones et swings etendus a droite jusqu'au dernier prix (redessines a
	/// chaque cloture primaire). Chart attendu : template ETH CME.
	/// </summary>
	public class MGIHtfInitialZone : Indicator
	{
		private class ZoneSignal
		{
			public string Tag;
			public DateTime ZoneStart;   // open de l'initial candle
			public DateTime SwingStart;  // open de la trendcandle (= close initial)
			public double Vah = double.NaN;
			public double Val = double.NaN;
			public bool HasZone;
			public double Swing = double.NaN;
			public bool Bullish;
		}

		private readonly List<ZoneSignal> _daily = new List<ZoneSignal>();
		private readonly List<ZoneSignal> _weekly = new List<ZoneSignal>();
		private DateTime _lastDailyClose = DateTime.MinValue;
		private DateTime _lastWeeklyClose = DateTime.MinValue;
		private bool _warnedNoMin;
		private bool _warnedHistD;
		private bool _warnedHistW;
		// Jours calendaires conseilles : sessions requises converties (6 seances/sem. dim-ven) + marge
		private int MinDaysRequired()
		{
			int needD = AtrDailyPeriod + 3;
			int needW = AtrWeeklyPeriod + 3;
			int daysD = (int)Math.Ceiling(needD * 7.0 / 6.0);
			int daysW = needW * 7 + 15;
			return Math.Max(daysD, daysW);
		}

		public override string DisplayName => Name;

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Description = @"MGI HTF InitialZone : zones de valeur (VA 1-min) des initial candles + swings PH/PB des trend-candles Daily (ATR20) et Weekly (ATR12). Template ETH requis. Chargez au moins 120 jours (daily + weekly).";
				Name = "MGIHtfInitialZone";
				Calculate = Calculate.OnBarClose;
				IsOverlay = true;
				DisplayInDataBox = false;
				DrawOnPricePanel = true;
				IsSuspendedWhileInactive = true;
				PaintPriceMarkers = false;
				IsAutoScale = false;
				BarsRequiredToPlot = 2;
				MaximumBarsLookBack = MaximumBarsLookBack.Infinite; // VA hebdo = ~10k barres 1-min
				AtrDailyPeriod = 20;
				AtrWeeklyPeriod = 12;
				AtrMultiplier = 1.0;
				ValueAreaPct = 70;
				RowHeightTicks = 4;
				ShowDaily = true;
				ShowWeekly = true;
				ShowZones = true;
				ShowSwings = true;
				MaxDailyZones = 5;
				MaxWeeklyZones = 5;
				DailyColor = Brushes.Red;
				WeeklyColor = Brushes.DodgerBlue;
				ZoneOpacity = 20;
				ZoneBorderWidth = 1;
				SwingWidth = 2;
				DailySwingDashed = false;
				WeeklySwingDashed = true;
			}
		else if (State == State.Configure)
		{
			// NT 8.1 impose Configure (SetDefaults = "cannot be called from this state")
			try { AddDataSeries(BarsPeriodType.Day, 1); }    // BIP 1
			catch (Exception ex) { try { Print(Name + " : serie Daily refusee (" + ex.Message + ")."); } catch {} }
			try { AddDataSeries(BarsPeriodType.Week, 1); }   // BIP 2
			catch (Exception ex) { try { Print(Name + " : serie Weekly refusee (" + ex.Message + ")."); } catch {} }
			try { AddDataSeries(BarsPeriodType.Minute, 1); } // BIP 3 (volume profile)
			catch (Exception ex) { try { Print(Name + " : serie Minute refusee (" + ex.Message + ")."); } catch {} }
		}
		else if (State == State.DataLoaded)
		{
			_daily.Clear();
			_weekly.Clear();
			_lastDailyClose = DateTime.MinValue;
			_lastWeeklyClose = DateTime.MinValue;
			_warnedNoMin = false;
			_warnedHistD = false;
			_warnedHistW = false;
		}
		else if (State == State.Terminated)
			{
				_daily.Clear();
				_weekly.Clear();
			}
		}

		#region Moteur ATR Wilder (standard NT8)
		private double WilderAtr(int bip, int period, int endBarsAgo)
		{
			try
			{
				int cur = CurrentBars[bip];
				if (cur < period + endBarsAgo + 1) return double.NaN;
				// TR du plus ancien vers endBarsAgo, seed SMA puis lissage Wilder
				double seed = 0;
				double atr = double.NaN;
				// On accumule a l'envers : d'abord les `period` plus vieux pour le seed SMA
				int oldest = cur - 1; // Closes[oldest+1] doit exister
				int total = oldest - endBarsAgo + 1;
				if (total < period) return double.NaN;
				for (int ago = oldest; ago >= endBarsAgo; ago--)
				{
					double h = Highs[bip][ago], l = Lows[bip][ago], pc = Closes[bip][ago + 1];
					if (double.IsNaN(h) || double.IsNaN(l) || double.IsNaN(pc)) return double.NaN;
					double tr = Math.Max(h - l, Math.Max(Math.Abs(h - pc), Math.Abs(l - pc)));
					int done = oldest - ago; // 0-based depuis le plus vieux
					if (done < period)
					{
						seed += tr;
						if (done == period - 1) atr = seed / period;
					}
					else
						atr = (atr * (period - 1) + tr) / period;
				}
				return atr;
			} catch { return double.NaN; }
		}
		#endregion

		#region Value Area 1-min (rows de N ticks, volume uniforme)
		private bool BuildValueArea(DateTime startT, DateTime endT, out double vah, out double val)
		{
			vah = double.NaN; val = double.NaN;
			try
			{
				if (CurrentBars[3] < 1) return false;
				double tick = Instrument.MasterInstrument.TickSize;
				if (tick <= 0) return false;
				double rowSize = tick * Math.Max(1, RowHeightTicks);
				var vol = new Dictionary<int, double>();
				double pHigh = double.MinValue, pLow = double.MaxValue;
				bool any = false;
				for (int ago = 0; ago <= CurrentBars[3]; ago++)
				{
					DateTime t;
					try { t = Times[3][ago]; } catch { break; }
					if (t <= startT) break;            // plus vieux que l'initial candle : fini
					if (t > endT) continue;            // barres posterieures : ignorees
					double h = Highs[3][ago], l = Lows[3][ago], v = Volumes[3][ago];
					if (double.IsNaN(h) || double.IsNaN(l) || double.IsNaN(v) || v <= 0) continue;
					any = true;
					if (h > pHigh) pHigh = h;
					if (l < pLow) pLow = l;
					int lo = (int)Math.Floor(l / rowSize);
					int hi = (int)Math.Floor(h / rowSize);
					int n = Math.Max(1, hi - lo + 1);
					double q = v / n;
					for (int k = lo; k <= hi; k++)
						vol[k] = vol.ContainsKey(k) ? vol[k] + q : q;
				}
				if (!any || vol.Count == 0) return false;
				double mid = (pHigh + pLow) * 0.5;
				int poc = 0; double pv = double.MinValue; bool first = true;
				int minK = int.MaxValue, maxK = int.MinValue;
				double tot = 0;
				foreach (var kv in vol)
				{
					tot += kv.Value;
					if (kv.Key < minK) minK = kv.Key;
					if (kv.Key > maxK) maxK = kv.Key;
					double rowMid = (kv.Key + 0.5) * rowSize;
					if (first || kv.Value > pv || (kv.Value == pv && Math.Abs(rowMid - mid) < Math.Abs((poc + 0.5) * rowSize - mid)))
					{ poc = kv.Key; pv = kv.Value; first = false; }
				}
				int loK = poc, hiK = poc;
				double acc = vol[poc];
				double target = tot * ValueAreaPct / 100.0;
				int guard = 0;
				while (acc < target && guard++ < 100000)
				{
					double up = (hiK + 1 <= maxK && vol.ContainsKey(hiK + 1)) ? vol[hiK + 1] : -1;
					double dn = (loK - 1 >= minK && vol.ContainsKey(loK - 1)) ? vol[loK - 1] : -1;
					if (up < 0 && dn < 0) break;
					if (up >= dn) { hiK++; if (vol.ContainsKey(hiK)) acc += vol[hiK]; }
					else { loK--; if (vol.ContainsKey(loK)) acc += vol[loK]; }
				}
				vah = (hiK + 1) * rowSize;
				val = loK * rowSize;
				return true;
			} catch { return false; }
		}
		#endregion

		#region Detection
		private void OnDailyClose()
		{
			try
			{
				if (CurrentBars[1] < AtrDailyPeriod + 3)
				{
					if (!_warnedHistD) { _warnedHistD = true; try { Print(Name + " : historique insuffisant — chargez au moins " + MinDaysRequired() + " jours (daily : " + CurrentBars[1] + "/" + (AtrDailyPeriod + 3) + " séances)."); } catch {} }
					return;
				}
				DateTime trendClose = Times[1][0];
				if (trendClose == _lastDailyClose) return;
				_lastDailyClose = trendClose;
				double o = Opens[1][0], c = Closes[1][0];
				if (double.IsNaN(o) || double.IsNaN(c)) return;
				double atr = WilderAtr(1, AtrDailyPeriod, 1); // ATR avant la trendcandle (pas biaise)
				if (double.IsNaN(atr) || atr <= 0) return;
				if (Math.Abs(c - o) <= atr * AtrMultiplier) return;
				bool bull = c > o;
				double swing = bull ? Lows[1][0] : Highs[1][0];
				if (double.IsNaN(swing)) return;
				DateTime initClose = Times[1][1];
				DateTime initOpen = CurrentBars[1] >= 2 ? Times[1][2] : initClose.AddDays(-1);
				double vah, val;
				bool ok = BuildValueArea(initOpen, initClose, out vah, out val);
				if (!ok && !_warnedNoMin) { _warnedNoMin = true; try { Print(Name + " : historique 1-min insuffisant pour une initial zone (chargez plus de jours)."); } catch {} }
			string tag = "MGIIZ_D_" + initClose.ToString("yyyyMMdd_HHmm");
			AddSignal(_daily, MaxDailyZones, new ZoneSignal
			{
				Tag = tag, ZoneStart = initOpen, SwingStart = initClose,
				Vah = vah, Val = val, HasZone = ok, Swing = swing, Bullish = bull
			});
		} catch {}
	}

		private void OnWeeklyClose()
		{
			try
			{
				if (CurrentBars[2] < AtrWeeklyPeriod + 3)
				{
					if (!_warnedHistW) { _warnedHistW = true; try { Print(Name + " : historique insuffisant — chargez au moins " + MinDaysRequired() + " jours (weekly : " + CurrentBars[2] + "/" + (AtrWeeklyPeriod + 3) + " semaines)."); } catch {} }
					return;
				}
				DateTime trendClose = Times[2][0];
				if (trendClose == _lastWeeklyClose) return;
				_lastWeeklyClose = trendClose;
				double o = Opens[2][0], c = Closes[2][0];
				if (double.IsNaN(o) || double.IsNaN(c)) return;
				double atr = WilderAtr(2, AtrWeeklyPeriod, 1);
				if (double.IsNaN(atr) || atr <= 0) return;
				if (Math.Abs(c - o) <= atr * AtrMultiplier) return;
				bool bull = c > o;
				double swing = bull ? Lows[2][0] : Highs[2][0];
				if (double.IsNaN(swing)) return;
				DateTime initClose = Times[2][1];
				DateTime initOpen = CurrentBars[2] >= 2 ? Times[2][2] : initClose.AddDays(-7);
				double vah, val;
				bool ok = BuildValueArea(initOpen, initClose, out vah, out val);
				if (!ok && !_warnedNoMin) { _warnedNoMin = true; try { Print(Name + " : historique 1-min insuffisant pour une initial zone (chargez plus de jours)."); } catch {} }
			string tag = "MGIIZ_W_" + initClose.ToString("yyyyMMdd_HHmm");
			AddSignal(_weekly, MaxWeeklyZones, new ZoneSignal
			{
				Tag = tag, ZoneStart = initOpen, SwingStart = initClose,
				Vah = vah, Val = val, HasZone = ok, Swing = swing, Bullish = bull
			});
		} catch {}
	}

		private void AddSignal(List<ZoneSignal> list, int maxKeep, ZoneSignal s)
		{
			list.Add(s);
			int max = Math.Max(1, maxKeep);
			while (list.Count > max)
			{
				try { RemoveDrawObject(list[0].Tag + "_Z"); } catch {}
				try { RemoveDrawObject(list[0].Tag + "_S"); } catch {}
				list.RemoveAt(0);
			}
			RedrawAll();
		}
		#endregion

		#region Dessin (extension jusqu'au dernier prix)
		private void RedrawAll()
		{
			try
			{
				if (BarsArray[0] == null || BarsArray[0].Count == 0 || CurrentBars[0] < 0) return;
				if (ShowDaily)
					foreach (var s in _daily) DrawSignal(s, DailyColor, DailySwingDashed, true);
				else
					foreach (var s in _daily) { try { RemoveDrawObject(s.Tag + "_Z"); } catch {} try { RemoveDrawObject(s.Tag + "_S"); } catch {} }
			if (ShowWeekly)
				foreach (var s in _weekly) DrawSignal(s, WeeklyColor, WeeklySwingDashed, true);
			else
				foreach (var s in _weekly) { try { RemoveDrawObject(s.Tag + "_Z"); } catch {} try { RemoveDrawObject(s.Tag + "_S"); } catch {} }
		} catch {}
	}

		private void DrawSignal(ZoneSignal s, Brush color, bool dashed, bool visible)
		{
			DateTime endT;
			try { endT = Times[0][0]; } catch { return; }
			if (!visible) return;
			if (ShowZones && s.HasZone && !double.IsNaN(s.Vah) && !double.IsNaN(s.Val) && s.Val < s.Vah && endT > s.ZoneStart)
			{
				try
				{
					var rect = Draw.Rectangle(this, s.Tag + "_Z", false, s.ZoneStart, s.Vah, endT, s.Val, color, color, ZoneOpacity);
					if (rect != null && rect.OutlineStroke != null)
						rect.OutlineStroke = new Stroke(color, DashStyleHelper.Solid, ZoneBorderWidth) { RenderTarget = rect.OutlineStroke.RenderTarget };
				} catch {}
			}
			else if (!ShowZones) { try { RemoveDrawObject(s.Tag + "_Z"); } catch {} }
			if (ShowSwings && !double.IsNaN(s.Swing) && endT > s.SwingStart)
			{
				try
				{
					Draw.Line(this, s.Tag + "_S", false, s.SwingStart, s.Swing, endT, s.Swing, color, dashed ? DashStyleHelper.Dash : DashStyleHelper.Solid, SwingWidth);
				} catch {}
			}
			else if (!ShowSwings) { try { RemoveDrawObject(s.Tag + "_S"); } catch {} }
		}
		#endregion

		protected override void OnBarUpdate()
		{
			try
			{
				if (BarsInProgress == 1) { OnDailyClose(); return; }
				if (BarsInProgress == 2) { OnWeeklyClose(); return; }
				if (BarsInProgress != 0) return;
				RedrawAll();
			} catch {}
	}

		#region Properties - Prerequis
		[NinjaScriptProperty]
		[ReadOnly(true)]
		[Display(Name = "Historique minimum requis", Description = "Chargez au moins ce nombre de jours (Days to load) : sinon daily et/ou weekly restent sans signal.", Order = 1, GroupName = "00 - Prerequis")]
		public string InfoHistoriqueMinimum
		{
			get
			{
				try
				{
					int needD = AtrDailyPeriod + 3;
					int needW = AtrWeeklyPeriod + 3;
					int daysD = (int)Math.Ceiling(needD * 7.0 / 6.0);
					return "Chargez au moins " + MinDaysRequired() + " jours : daily OK des ~" + daysD + "j (" + needD + " seances), weekly des ~" + MinDaysRequired() + "j (" + needW + " sem.).";
				} catch { return "Chargez au moins 120 jours (daily + weekly)."; }
			}
			set { }
		}
		#endregion

		#region Properties - Seuils
		[NinjaScriptProperty]
		[Display(Name = "ATR Daily period", Order = 1, GroupName = "01 - Seuils")]
		[Range(1, 100)]
		public int AtrDailyPeriod { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "ATR Weekly period", Order = 2, GroupName = "01 - Seuils")]
		[Range(1, 100)]
		public int AtrWeeklyPeriod { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Multiplicateur ATR (body > ATR*x)", Order = 3, GroupName = "01 - Seuils")]
		[Range(0.1, 5)]
		public double AtrMultiplier { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Value Area %", Order = 4, GroupName = "01 - Seuils")]
		[Range(50, 95)]
		public double ValueAreaPct { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Hauteur row profil (ticks)", Order = 5, GroupName = "01 - Seuils")]
		[Range(1, 32)]
		public int RowHeightTicks { get; set; }
		#endregion

		#region Properties - Affichage
		[NinjaScriptProperty]
		[Display(Name = "Afficher Daily", Order = 1, GroupName = "02 - Affichage")]
		public bool ShowDaily { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Afficher Weekly", Order = 2, GroupName = "02 - Affichage")]
		public bool ShowWeekly { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Afficher zones", Order = 3, GroupName = "02 - Affichage")]
		public bool ShowZones { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Afficher swings", Order = 4, GroupName = "02 - Affichage")]
		public bool ShowSwings { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Max zones Daily", Order = 5, GroupName = "02 - Affichage")]
		[Range(1, 50)]
		public int MaxDailyZones { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Max zones Weekly", Order = 6, GroupName = "02 - Affichage")]
		[Range(1, 50)]
		public int MaxWeeklyZones { get; set; }
		#endregion

		#region Properties - Style
		[NinjaScriptProperty]
		[XmlIgnore]
		[Display(Name = "Couleur Daily", Order = 1, GroupName = "03 - Style")]
		public Brush DailyColor { get; set; }

		[Browsable(false)]
		public string DailyColorSerializable
		{
			get { return Serialize.BrushToString(DailyColor); }
			set { DailyColor = Serialize.StringToBrush(value); }
		}

		[NinjaScriptProperty]
		[XmlIgnore]
		[Display(Name = "Couleur Weekly", Order = 2, GroupName = "03 - Style")]
		public Brush WeeklyColor { get; set; }

		[Browsable(false)]
		public string WeeklyColorSerializable
		{
			get { return Serialize.BrushToString(WeeklyColor); }
			set { WeeklyColor = Serialize.StringToBrush(value); }
		}

		[NinjaScriptProperty]
		[Display(Name = "Opacite zone (0-100)", Order = 3, GroupName = "03 - Style")]
		[Range(0, 100)]
		public int ZoneOpacity { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Epaisseur bordure zone", Order = 4, GroupName = "03 - Style")]
		[Range(1, 5)]
		public int ZoneBorderWidth { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Epaisseur swing", Order = 5, GroupName = "03 - Style")]
		[Range(1, 5)]
		public int SwingWidth { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Swing Daily pointille", Order = 6, GroupName = "03 - Style")]
		public bool DailySwingDashed { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Swing Weekly pointille", Order = 7, GroupName = "03 - Style")]
		public bool WeeklySwingDashed { get; set; }
		#endregion
	}
}
