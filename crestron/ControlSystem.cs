// =============================================================================
// Conference Room 2B: SIMPL# Pro reference program (4-Series processor)
//
// This is the Crestron-side version of avsim/room.py. It uses the SAME join
// numbers (see README.md) and the SAME PJLink commands, so the Python
// simulator can stand in for the real projector while you test.
//
// NOTE: written as a reference and NOT compiled here. Building it needs the
// Crestron SIMPL# Pro SDK (Crestron dealer/developer access). Check API
// details against the SDK version you build with.
// =============================================================================
using System;
using System.Text;
using Crestron.SimplSharp;
using Crestron.SimplSharp.CrestronSockets;
using Crestron.SimplSharpPro;
using Crestron.SimplSharpPro.CrestronThread;
using Crestron.SimplSharpPro.DeviceSupport;
using Crestron.SimplSharpPro.UI;

namespace RoomControl
{
    public class ControlSystem : CrestronControlSystem
    {
        // --- Join map (must match avsim/joins.py) ----------------------------
        const uint DigSystemOn = 1, DigSystemOff = 2, DigWarming = 3, DigCooling = 4;
        const uint DigVolUp = 5, DigVolDown = 6, DigMute = 7, DigSourceBase = 10;
        const uint DigProjOnline = 21;
        const uint AnaVolume = 1;
        const uint SerStatus = 2;

        const string ProjectorIp = "192.168.1.50";   // or the PC running run.py
        const int PjLinkPort = 4352;
        const ushort VolumeStep = 3277;                // ~5 % of 65535

        private Tsw770 _panel;
        private TCPClient _projector;
        private CTimer _pollTimer;
        private ushort _volume = 26214;                // 40 %
        private bool _muted;

        public ControlSystem() : base()
        {
            Thread.MaxNumberOfUserThreads = 20;
        }

        public override void InitializeSystem()
        {
            // Touch panel on IP ID 03
            _panel = new Tsw770(0x03, this);
            _panel.SigChange += OnPanelSigChange;
            if (_panel.Register() != eDeviceRegistrationUnRegistrationResponse.Success)
                ErrorLog.Error("Touch panel failed to register: {0}", _panel.RegistrationFailureReason);

            // Projector over PJLink
            _projector = new TCPClient(ProjectorIp, PjLinkPort, 1024);
            _projector.SocketStatusChange += (client, status) =>
                _panel.BooleanInput[DigProjOnline].BoolValue = status == SocketStatus.SOCKET_STATUS_CONNECTED;
            _projector.ConnectToServerAsync(OnProjectorConnected);

            // Poll projector power every 5 s for feedback
            _pollTimer = new CTimer(_ => SendPjLink("POWR ?"), null, 5000, 5000);
            SetStatus("Ready");
        }

        // --- Panel button presses --------------------------------------------
        private void OnPanelSigChange(BasicTriggerList device, SigEventArgs args)
        {
            if (args.Sig.Type != eSigType.Bool || !args.Sig.BoolValue) return; // rising edge only
            uint join = args.Sig.Number;

            if (join == DigSystemOn) SystemOn();
            else if (join == DigSystemOff) SystemOff();
            else if (join == DigVolUp) SetVolume((ushort)Math.Min(65535, _volume + VolumeStep));
            else if (join == DigVolDown) SetVolume((ushort)Math.Max(0, _volume - VolumeStep));
            else if (join == DigMute) { _muted = !_muted; _panel.BooleanInput[DigMute].BoolValue = _muted; }
            else if (join > DigSourceBase && join <= DigSourceBase + 4) SelectSource(join - DigSourceBase);
        }

        // --- Macros ----------------------------------------------------------
        private void SystemOn()
        {
            SetStatus("System On: projector warming up");
            _panel.BooleanInput[DigWarming].BoolValue = true;
            SendPjLink("POWR 1");
            // Input can't change during warm-up (PJLink ERR3); wait, then select HDMI 1.
            new CTimer(_ =>
            {
                SendPjLink("INPT 31");
                _panel.BooleanInput[DigWarming].BoolValue = false;
                _panel.BooleanInput[DigSystemOn].BoolValue = true;
                _panel.BooleanInput[DigSystemOff].BoolValue = false;
                SelectSource(1);
                SetStatus("System On: ready");
            }, 30000);
        }

        private void SystemOff()
        {
            SetStatus("System Off: cooling down");
            SendPjLink("POWR 0");
            _panel.BooleanInput[DigSystemOn].BoolValue = false;
            _panel.BooleanInput[DigSystemOff].BoolValue = true;
            for (uint i = 1; i <= 4; i++) _panel.BooleanInput[DigSourceBase + i].BoolValue = false;
        }

        private void SelectSource(uint input)
        {
            // Interlock: only one source button lit at a time.
            for (uint i = 1; i <= 4; i++) _panel.BooleanInput[DigSourceBase + i].BoolValue = i == input;
            // Switcher routing would go here (DM/HD-MD symbol or its TCP API).
        }

        private void SetVolume(ushort level)
        {
            _volume = level;
            _panel.UShortInput[AnaVolume].UShortValue = level;
            // DSP command would go here.
        }

        // --- PJLink transport ------------------------------------------------
        private void OnProjectorConnected(TCPClient client)
        {
            if (client.ClientStatus == SocketStatus.SOCKET_STATUS_CONNECTED)
                client.ReceiveDataAsync(OnProjectorData);
            else
                new CTimer(_ => client.ConnectToServerAsync(OnProjectorConnected), 5000); // retry
        }

        private void SendPjLink(string body)
        {
            if (_projector.ClientStatus != SocketStatus.SOCKET_STATUS_CONNECTED) return;
            byte[] data = Encoding.ASCII.GetBytes("%1" + body + "\r");
            _projector.SendDataAsync(data, data.Length, (c, n) => { });
        }

        private void OnProjectorData(TCPClient client, int length)
        {
            if (length <= 0) { client.ConnectToServerAsync(OnProjectorConnected); return; }
            string reply = Encoding.ASCII.GetString(client.IncomingDataBuffer, 0, length).Trim();
            if (reply.StartsWith("%1POWR="))
            {
                string state = reply.Substring(7);
                _panel.BooleanInput[DigWarming].BoolValue = state == "3";
                _panel.BooleanInput[DigCooling].BoolValue = state == "2";
            }
            else if (reply.Contains("=ERR"))
            {
                ErrorLog.Warn("Projector error: {0}", reply);
            }
            client.ReceiveDataAsync(OnProjectorData);
        }

        private void SetStatus(string text)
        {
            _panel.StringInput[SerStatus].StringValue = text;
            CrestronConsole.PrintLine("[Room] " + text);
        }
    }
}
