RACE ENGINEER - ASSETTO CORSA APP

Enable RaceEngineer in Content Manager > Settings > Assetto Corsa > Apps.
Enable APEX session bridge in the CSP App Shelf for expanded timing and tyres.
Enter a new driving session after enabling the apps.
In a driving session, open the right-hand app sidebar and show Race Engineer.
Start the APEX desktop companion on the same PC and choose Start session.

The overlay explicitly labels sample data as DEMO. Select Live in the companion
to use real game telemetry. If the companion stops responding, readings clear
and COMPANION OFFLINE is shown. Garage setup values are not applied automatically.

Click ENGINEER MENU + for live questions about fuel, tyres, pace, corners,
strategy, energy, track limits and feedback. Race questions include your best,
race fastest, other drivers' best laps and gaps ahead, behind and to the leader.
Change compound asks for an option and can prepare the next Quick Pit service
when the CSP bridge reports a compatible editable tyre selector. It waits for
the game to acknowledge the selection; mounted tyres do not change on track.
Quiet/more/fewer controls change APEX radio preferences only. Requests are
queued on the game thread and sent by the background worker. Answers appear
in the engineer panel and are spoken when desktop voice is enabled.

In desktop Settings, choose a keyboard key (F10 by default), or choose Bind wheel
button... and press your chosen G29 or other detected controller button. Save
preferences. Release the button before starting; learning does not open the mic.
Hold the configured button, wait for MIC LISTENING, then say an English command
such as fuel status, next corner, feedback, my fastest lap, gap ahead, or change
to hard. Release to stop input and hear the answer. Microphone audio is
processed locally and is not saved. The overlay footer shows microphone startup
and listening status. Keep the desktop app running for input and spoken replies.

When available, timing for up to 64 cars is sent to the local companion. The
Python app provides names, lap times, track progress and speed. The CSP bridge
adds reported best laps, positions, current compounds and validity. Opponent
fuel, tyre pressures, temperatures, wear and setups are not supplied. Unknown
fields are omitted and stale samples are discarded. Race gaps are estimates;
practice and qualifying best-lap differences are not treated as physical gaps.

Compound selection was tested in AC 1.16.4 / CSP 0.3.0-preview581 with the Kunos
Porsche 911 GT3 R 2016 at Red Bull Ring GP: No change=-1, S=0, M=1, H=2.
Other cars require the same compatible numeric range; every mod and compound
order has not been individually verified. See csp_app/ApexBridge/README.md in
the desktop package for the bridge details and current limitations.
