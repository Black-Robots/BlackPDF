; Instalador de BlackPDF para Windows (Inno Setup 6.7).
; Compilar: ISCC.exe /DMyAppVersion=1.2.0 /DSourceDir=..\..\build\dist-win\BlackPDF BlackPDF.iss
; Firmar (cuando haya certificado): ISCC.exe /Ssigntool="signtool.exe sign /fd sha256 ... $f" /DSignTool=signtool ...
;
; Actualizar: el mismo AppId instala encima de la versión anterior (misma carpeta, mismas opciones)
; y conserva las preferencias. Actualización desatendida, la que usará la propia aplicación:
;   BlackPDF-x.y.z-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /relaunch=1
; cierra BlackPDF si está abierto, actualiza y lo vuelve a abrir.

#ifndef MyAppVersion
  #define MyAppVersion "1.2.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\build\dist-win\BlackPDF"
#endif
#ifndef OutputDir
  #define OutputDir "..\out"
#endif

#define MyAppName "BlackPDF"
#define MyAppExe "BlackPDF.exe"
#define MyAppPublisher "The Black Robots Foundation"
#define MyAppURL "https://blackrobots.org/blackpdf"
#define MyRepoURL "https://github.com/Black-Robots/BlackPDF"
#define MyProgId "BlackPDF.Document"

[Setup]
AppId={{6C3B2E1A-8F4D-4B7E-9A51-2D7F0C9B4E21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyRepoURL}/issues
AppUpdatesURL={#MyRepoURL}/releases
AppCopyright=Free software under the AGPL-3.0 license
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=BlackPDF-{#MyAppVersion}-Setup
SetupIconFile=blackpdf.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
; Claro u oscuro según Windows, como la aplicación, sin líneas separadoras.
WizardStyle=modern dynamic windows11 excludelightcontrols hidebevels
WizardSizePercent=110
; Página de bienvenida con el panel de la marca (también en la final).
DisableWelcomePage=no
WizardImageFile=wizard\large-100.png,wizard\large-125.png,wizard\large-150.png,wizard\large-175.png,wizard\large-200.png,wizard\large-225.png,wizard\large-250.png
WizardImageFileDynamicDark=wizard\large-100.png,wizard\large-125.png,wizard\large-150.png,wizard\large-175.png,wizard\large-200.png,wizard\large-225.png,wizard\large-250.png
WizardImageBackColor=#0A0A0B
WizardImageBackColorDynamicDark=#0A0A0B
WizardSmallImageFile=wizard\small-100.png,wizard\small-125.png,wizard\small-150.png,wizard\small-175.png,wizard\small-200.png,wizard\small-225.png,wizard\small-250.png
WizardSmallImageFileDynamicDark=wizard\small-100.png,wizard\small-125.png,wizard\small-150.png,wizard\small-175.png,wizard\small-200.png,wizard\small-225.png,wizard\small-250.png
; Fondo de las páginas con los colores de la aplicación (packaging/windows/wizard_images.py).
WizardBackImageFile=wizard\back-light-100.png,wizard\back-light-125.png,wizard\back-light-150.png,wizard\back-light-175.png,wizard\back-light-200.png,wizard\back-light-225.png,wizard\back-light-250.png
WizardBackImageFileDynamicDark=wizard\back-dark-100.png,wizard\back-dark-125.png,wizard\back-dark-150.png,wizard\back-dark-175.png,wizard\back-dark-200.png,wizard\back-dark-225.png,wizard\back-dark-250.png
; Al actualizar no se vuelve a preguntar la carpeta.
DisableDirPage=auto
; Nunca dos instalaciones a la vez (por ejemplo, una actualización y otra a mano).
SetupMutex=BlackPDFSetup,Global\BlackPDFSetup
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Qt 6 usa la ICU de Windows (icuuc.dll), disponible desde Windows 10 1903.
MinVersion=10.0.18362
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoDescription={#MyAppName} Setup
VersionInfoCompany={#MyAppPublisher}
#ifdef SignTool
SignTool={#SignTool}
SignedUninstaller=yes
#endif

[Languages]
; El instalador elige el idioma de Windows; la aplicación hace lo mismo al arrancar.
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "portuguese"; MessagesFile: "compiler:Languages\Portuguese.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Messages]
; Textos propios: más cercanos que los de serie y, en español, de tú como la aplicación.
english.WelcomeLabel1=Welcome to [name]
english.WelcomeLabel2=This will install [name/ver] on your computer: a free and open PDF viewer and editor that works offline.%n%nYour documents never leave your computer.
english.FinishedHeadingLabel=[name] is ready
english.FinishedLabelNoIcons=[name] has been installed on your computer.
english.FinishedLabel=[name] has been installed on your computer. Open it from the Start menu, or use "Open with" on any PDF.
brazilianportuguese.WelcomeLabel1=Boas-vindas ao [name]
brazilianportuguese.WelcomeLabel2=O [name/ver] será instalado no seu computador: um visualizador e editor de PDF livre, gratuito e sem conexão.%n%nSeus documentos nunca saem do seu computador.
brazilianportuguese.FinishedHeadingLabel=O [name] está pronto
brazilianportuguese.FinishedLabelNoIcons=O [name] foi instalado no seu computador.
brazilianportuguese.FinishedLabel=O [name] foi instalado no seu computador. Abra-o pelo menu Iniciar ou use "Abrir com" em qualquer PDF.
portuguese.WelcomeLabel1=Boas-vindas ao [name]
portuguese.WelcomeLabel2=O [name/ver] vai ser instalado no seu computador: um visualizador e editor de PDF livre, gratuito e sem ligação à Internet.%n%nOs seus documentos nunca saem do seu computador.
portuguese.FinishedHeadingLabel=O [name] está pronto
portuguese.FinishedLabelNoIcons=O [name] foi instalado no seu computador.
portuguese.FinishedLabel=O [name] foi instalado no seu computador. Abra-o a partir do menu Iniciar ou utilize "Abrir com" em qualquer PDF.
french.WelcomeLabel1=Bienvenue dans [name]
french.WelcomeLabel2=[name/ver] va être installé sur votre ordinateur : une visionneuse et un éditeur PDF libres, gratuits et hors ligne.%n%nVos documents ne quittent jamais votre ordinateur.
french.FinishedHeadingLabel=[name] est prêt
french.FinishedLabelNoIcons=[name] a été installé sur votre ordinateur.
french.FinishedLabel=[name] a été installé sur votre ordinateur. Ouvrez-le depuis le menu Démarrer ou utilisez « Ouvrir avec » sur n'importe quel PDF.
spanish.WelcomeLabel1=Te damos la bienvenida a [name]
spanish.WelcomeLabel2=Se instalará [name/ver] en tu equipo: un visor y editor de PDF libre, gratuito y sin conexión.%n%nTus documentos nunca salen de tu equipo.
spanish.ClickNext=Pulsa Siguiente para continuar o Cancelar para salir.
spanish.PrivilegesRequiredOverrideTitle=Modo de instalación
spanish.PrivilegesRequiredOverrideInstruction=Elige cómo instalar BlackPDF
spanish.PrivilegesRequiredOverrideText1=%1 se puede instalar para todos los usuarios (necesita permisos de administrador) o solo para ti.
spanish.PrivilegesRequiredOverrideText2=%1 se puede instalar solo para ti o para todos los usuarios (necesita permisos de administrador).
spanish.PrivilegesRequiredOverrideAllUsers=Instalar para &todos los usuarios
spanish.PrivilegesRequiredOverrideAllUsersRecommended=Instalar para &todos los usuarios (recomendado)
spanish.PrivilegesRequiredOverrideCurrentUser=Instalar solo para &mí
spanish.PrivilegesRequiredOverrideCurrentUserRecommended=Instalar solo para &mí (recomendado)
spanish.ExitSetupTitle=Salir de la instalación
spanish.ExitSetupMessage=La instalación no ha terminado. Si sales ahora, BlackPDF no se instalará.%n%nPuedes volver a abrir el instalador cuando quieras.%n%n¿Salir de la instalación?
spanish.WizardSelectDir=Carpeta de instalación
spanish.SelectDirDesc=¿Dónde quieres instalar [name]?
spanish.SelectDirLabel3=[name] se instalará en esta carpeta.
spanish.SelectDirBrowseLabel=Pulsa Siguiente para continuar o Examinar para elegir otra carpeta.
spanish.WizardSelectTasks=Opciones
spanish.SelectTasksDesc=¿Qué más quieres que haga el instalador?
spanish.SelectTasksLabel2=Elige las opciones que quieras y pulsa Siguiente.
spanish.WizardReady=Todo listo para instalar
spanish.ReadyLabel1=[name] está listo para instalarse en tu equipo.
spanish.ReadyLabel2a=Pulsa Instalar para continuar, o Atrás si quieres revisar alguna opción.
spanish.ReadyLabel2b=Pulsa Instalar para continuar.
spanish.ReadyMemoDir=Carpeta de instalación:
spanish.ReadyMemoGroup=Carpeta del menú Inicio:
spanish.ReadyMemoTasks=Opciones:
spanish.WizardPreparing=Preparando la instalación
spanish.PreparingDesc=Preparando la instalación de [name] en tu equipo.
spanish.ApplicationsFound=Estas aplicaciones están usando archivos que hay que actualizar. Es recomendable dejar que el instalador las cierre.
spanish.ApplicationsFound2=Estas aplicaciones están usando archivos que hay que actualizar. Es recomendable dejar que el instalador las cierre; cuando termine, intentará volver a abrirlas.
spanish.CloseApplications=&Cerrar las aplicaciones automáticamente
spanish.DontCloseApplications=&No cerrar las aplicaciones
spanish.InstallingLabel=Espera mientras se instala [name] en tu equipo.
spanish.FinishedHeadingLabel=[name] está listo
spanish.FinishedLabelNoIcons=[name] se ha instalado en tu equipo.
spanish.FinishedLabel=[name] se ha instalado en tu equipo. Ábrelo desde el menú Inicio o con «Abrir con» en cualquier PDF.
spanish.ClickFinish=Pulsa Finalizar para cerrar el instalador.
spanish.FinishedRestartLabel=Para terminar de instalar [name] hay que reiniciar el equipo. ¿Reiniciarlo ahora?
spanish.FinishedRestartMessage=Para terminar de instalar [name] hay que reiniciar el equipo.%n%n¿Reiniciarlo ahora?
spanish.ConfirmUninstall=¿Seguro que quieres desinstalar %1? Tus preferencias se conservan.
spanish.UninstalledAll=%1 se ha desinstalado de tu equipo.
spanish.WizardUninstalling=Desinstalando
spanish.StatusUninstalling=Desinstalando %1...

[CustomMessages]
english.AssocTask=Show BlackPDF in "Open with" for PDF files
spanish.AssocTask=Mostrar BlackPDF en «Abrir con» para los archivos PDF
brazilianportuguese.AssocTask=Mostrar o BlackPDF em "Abrir com" para arquivos PDF
portuguese.AssocTask=Mostrar o BlackPDF em "Abrir com" para ficheiros PDF
french.AssocTask=Afficher BlackPDF dans « Ouvrir avec » pour les fichiers PDF
english.DefaultTask=Open settings to choose BlackPDF as the default PDF reader
spanish.DefaultTask=Abrir la configuración para elegir BlackPDF como lector de PDF predeterminado
brazilianportuguese.DefaultTask=Abrir as configurações para escolher o BlackPDF como leitor de PDF padrão
portuguese.DefaultTask=Abrir as definições para escolher o BlackPDF como leitor de PDF predefinido
french.DefaultTask=Ouvrir les paramètres pour choisir BlackPDF comme lecteur PDF par défaut
english.DocName=PDF document
spanish.DocName=Documento PDF
brazilianportuguese.DocName=Documento PDF
portuguese.DocName=Documento PDF
french.DocName=Document PDF
english.AppDescription=Free offline PDF viewer and editor
spanish.AppDescription=Visor y editor de PDF libre y sin conexión
brazilianportuguese.AppDescription=Visualizador e editor de PDF livre e sem conexão
portuguese.AppDescription=Visualizador e editor de PDF livre e sem ligação à Internet
french.AppDescription=Visionneuse et éditeur PDF libre et hors ligne
english.AssocGroup=PDF files:
spanish.AssocGroup=Archivos PDF:
brazilianportuguese.AssocGroup=Arquivos PDF:
portuguese.AssocGroup=Ficheiros PDF:
french.AssocGroup=Fichiers PDF :
english.LaunchApp=Launch BlackPDF
spanish.LaunchApp=Abrir BlackPDF
brazilianportuguese.LaunchApp=Abrir o BlackPDF
portuguese.LaunchApp=Abrir o BlackPDF
french.LaunchApp=Lancer BlackPDF

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "associate"; Description: "{cm:AssocTask}"; GroupDescription: "{cm:AssocGroup}"
Name: "makedefault"; Description: "{cm:DefaultTask}"; GroupDescription: "{cm:AssocGroup}"; Flags: unchecked

[InstallDelete]
; Al actualizar se borra la versión anterior de las bibliotecas: así no quedan archivos
; de versiones viejas mezclados con los nuevos.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; AppUserModelID: "BlackPDF.BlackPDF"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon; AppUserModelID: "BlackPDF.BlackPDF"

[Registry]
; Tipo de documento propio, con icono y verbo «Abrir».
Root: HKA; Subkey: "Software\Classes\{#MyProgId}"; ValueType: string; ValueName: ""; ValueData: "{cm:DocName}"; Flags: uninsdeletekey; Tasks: associate
Root: HKA; Subkey: "Software\Classes\{#MyProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExe}"",0"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\{#MyProgId}\shell\open"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppName}"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\{#MyProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExe}"" ""%1"""; Tasks: associate
; Aparecer en «Abrir con» de los PDF.
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "{#MyProgId}"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associate
; Registro de la aplicación (nombre visible y tipos admitidos).
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExe}\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExe}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExe}"" ""%1"""
; Capacidades para «Aplicaciones predeterminadas» de Windows.
Root: HKA; Subkey: "Software\{#MyAppName}\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\{#MyAppName}\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "{cm:AppDescription}"
Root: HKA; Subkey: "Software\{#MyAppName}\Capabilities"; ValueType: string; ValueName: "ApplicationIcon"; ValueData: """{app}\{#MyAppExe}"",0"
Root: HKA; Subkey: "Software\{#MyAppName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".pdf"; ValueData: "{#MyProgId}"
Root: HKA; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: "Software\{#MyAppName}\Capabilities"; Flags: uninsdeletevalue
; Ruta de la aplicación para poder lanzarla con Win+R «blackpdf».
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#MyAppExe}"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExe}"; Flags: uninsdeletekey

[Run]
Filename: "ms-settings:defaultapps"; Flags: shellexec nowait postinstall skipifsilent; Tasks: makedefault; Description: "{cm:DefaultTask}"
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchApp}"; Flags: nowait postinstall skipifsilent
; Tras una actualización desatendida (/relaunch=1) se vuelve a abrir BlackPDF.
Filename: "{app}\{#MyAppExe}"; Flags: nowait runasoriginaluser; Check: RelaunchAfterUpdate

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
; Caché de la interfaz (las preferencias del usuario se conservan).
Type: filesandordirs; Name: "{localappdata}\BlackPDF\BlackPDF\cache"

[Code]
function RelaunchAfterUpdate: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:relaunch|0}') = '1');
end;
