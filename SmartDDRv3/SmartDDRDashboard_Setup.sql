/* =====================================================================================
   SmartDDR Dashboard - database objects for Trend history, Baselines and Saved views.
   Run once in the ACAD_DATA database (safe to re-run). SQL Server 2016+.

   Creates
     dbo.SDDR_DASH_SNAPSHOT        nightly KPI snapshot per project and discipline ('*' = project total)
     dbo.SDDR_DASH_BASELINE        frozen copies of the planned dates (named baselines)
     dbo.SDDR_DASH_VIEWS           saved / shared dashboard views
     dbo.usp_SDDR_Dash_Snapshot    takes today's snapshot (all projects, or one)
     dbo.usp_SDDR_Dash_Baseline    captures a named baseline for a project
   Optional (bottom): SQL Server Agent job that runs the snapshot every night.

   The snapshot logic mirrors the dashboard defaults:
     - activity rows (Document_No LIKE '%ACTIVI%') and cancelled rows (DELETED / CANCELLED / VOID /
       SUPERSEDED in status or remarks) are excluded;
     - complete  = AFC actual <= snapshot date, or status AFC / AFX / ADH, or status = required status;
     - next milestone = first stage after the last achieved one that has a planned date;
       overdue = next milestone planned before the snapshot date;
     - earned hours = [Used Hours]; planned hours = Hours x PLN% (PLN% stored as 0-1 or 0-100).
   ===================================================================================== */
SET NOCOUNT ON;
GO

IF OBJECT_ID('dbo.SDDR_DASH_SNAPSHOT', 'U') IS NULL
CREATE TABLE dbo.SDDR_DASH_SNAPSHOT (
    SnapDate      date          NOT NULL,
    Project_No    varchar(40)   NOT NULL,
    Discipline    varchar(100)  NOT NULL,      -- '*' = whole project
    Docs          int           NOT NULL,
    Complete      int           NOT NULL,
    Pending       int           NOT NULL,
    Overdue       int           NOT NULL,
    Due14         int           NOT NULL,
    NotStarted    int           NOT NULL,
    EstHours      decimal(18,2) NOT NULL,
    EarnedHours   decimal(18,2) NOT NULL,
    PlannedHours  decimal(18,2) NOT NULL,
    M75Plan       int           NOT NULL,
    M75Done       int           NOT NULL,
    CreatedAt     datetime2(0)  NOT NULL CONSTRAINT DF_SDDR_DASH_SNAPSHOT_CreatedAt DEFAULT SYSDATETIME(),
    CONSTRAINT PK_SDDR_DASH_SNAPSHOT PRIMARY KEY (SnapDate, Project_No, Discipline)
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_SDDR_DASH_SNAPSHOT_Project')
    CREATE INDEX IX_SDDR_DASH_SNAPSHOT_Project ON dbo.SDDR_DASH_SNAPSHOT (Project_No, SnapDate);
GO

IF OBJECT_ID('dbo.SDDR_DASH_BASELINE', 'U') IS NULL
CREATE TABLE dbo.SDDR_DASH_BASELINE (
    BaselineName  varchar(60)   NOT NULL,
    Project_No    varchar(40)   NOT NULL,
    DocKey        varchar(150)  NOT NULL,      -- DDR_ID (or Document_No when DDR_ID is blank)
    DDR_ID        varchar(50)   NULL,
    Document_No   varchar(150)  NULL,
    IDC_PLN date NULL, IFR_PLN date NULL, RCC_PLN date NULL, IFR2_PLN date NULL,
    RCC2_PLN date NULL, APP_PLN date NULL, AFC_PLN date NULL,
    CapturedAt    datetime2(0)  NOT NULL,
    CapturedBy    varchar(100)  NULL,
    CONSTRAINT PK_SDDR_DASH_BASELINE PRIMARY KEY (Project_No, BaselineName, DocKey)
);
GO

IF OBJECT_ID('dbo.SDDR_DASH_VIEWS', 'U') IS NULL
CREATE TABLE dbo.SDDR_DASH_VIEWS (
    ViewId     int IDENTITY(1,1) NOT NULL CONSTRAINT PK_SDDR_DASH_VIEWS PRIMARY KEY,
    Name       varchar(60)    NOT NULL,
    Scope      varchar(60)    NULL,           -- e.g. 'P:3017' or 'G:3' (informational)
    StateJson  nvarchar(max)  NOT NULL,
    Owner      varchar(100)   NOT NULL,
    IsShared   bit            NOT NULL CONSTRAINT DF_SDDR_DASH_VIEWS_IsShared DEFAULT 0,
    CreatedAt  datetime2(0)   NOT NULL,
    UpdatedAt  datetime2(0)   NOT NULL,
    CONSTRAINT UQ_SDDR_DASH_VIEWS UNIQUE (Owner, Name)
);
GO

/* ------------------------------------------------------------------------------------
   Snapshot
   ------------------------------------------------------------------------------------ */
CREATE OR ALTER PROCEDURE dbo.usp_SDDR_Dash_Snapshot
    @Project  varchar(40) = NULL,     -- NULL = every project
    @SnapDate date        = NULL      -- NULL = today
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;   -- any error rolls the whole snapshot back
    SET @SnapDate = ISNULL(@SnapDate, CAST(GETDATE() AS date));

    -- PLN% may be stored as a fraction (0-1) or a percentage (0-100).
    DECLARE @plnDiv float =
        CASE WHEN (SELECT MAX(ABS(TRY_CONVERT(float, [PLN%]))) FROM dbo.SDDR_ACON_DDR_EPR
                   WHERE @Project IS NULL OR Project_No = @Project) > 1.0001 THEN 100.0 ELSE 1.0 END;

    ;WITH src AS (
        SELECT
            e.Project_No,
            ISNULL(NULLIF(LTRIM(RTRIM(e.Discipline)), ''), '(Blank)') AS Discipline,
            ISNULL(TRY_CONVERT(float, e.Hours), 0)        AS Hours,
            ISNULL(TRY_CONVERT(float, e.[Used Hours]), 0) AS Earned,
            TRY_CONVERT(float, e.[PLN%]) / @plnDiv        AS PlnFrac,
            UPPER(LTRIM(RTRIM(ISNULL(e.CURR_STATUS, ''))))  AS St,
            UPPER(LTRIM(RTRIM(ISNULL(e.STATUS_REQD, ''))))  AS Rq,
            -- dates (TRY_CONVERT copes with text columns; <= 1900 means "blank")
            NULLIF(TRY_CONVERT(date, e.IDC_PLN),  '19000101') AS P1, NULLIF(TRY_CONVERT(date, e.IDC_ACT),  '19000101') AS A1,
            NULLIF(TRY_CONVERT(date, e.IFR_PLN),  '19000101') AS P2, NULLIF(TRY_CONVERT(date, e.IFR1_ACT), '19000101') AS A2,
            NULLIF(TRY_CONVERT(date, e.RCC_PLN),  '19000101') AS P3, NULLIF(TRY_CONVERT(date, e.RCC1_ACT), '19000101') AS A3,
            NULLIF(TRY_CONVERT(date, e.IFR2_PLN), '19000101') AS P4, NULLIF(TRY_CONVERT(date, e.IFR2_ACT), '19000101') AS A4,
            NULLIF(TRY_CONVERT(date, e.RCC2_PLN), '19000101') AS P5, NULLIF(TRY_CONVERT(date, e.RCC2_ACT), '19000101') AS A5,
            NULLIF(TRY_CONVERT(date, e.APP_PLN),  '19000101') AS P6, NULLIF(TRY_CONVERT(date, e.APP_ACT),  '19000101') AS A6,
            NULLIF(TRY_CONVERT(date, e.AFC_PLN),  '19000101') AS P7, NULLIF(TRY_CONVERT(date, e.AFC_ACT),  '19000101') AS A7
        FROM dbo.SDDR_ACON_DDR_EPR e
        WHERE e.Document_No IS NOT NULL
          AND (@Project IS NULL OR e.Project_No = @Project)
          AND e.Document_No NOT LIKE '%ACTIVI%'
          AND ISNULL(e.CURR_STATUS, '') + ' ' + ISNULL(e.REMARKS, '') + ' ' + ISNULL(e.Doc_Status, '')
              NOT LIKE '%DELETED%'
          AND ISNULL(e.CURR_STATUS, '') + ' ' + ISNULL(e.REMARKS, '') NOT LIKE '%CANCEL%'
          AND ISNULL(e.CURR_STATUS, '') + ' ' + ISNULL(e.REMARKS, '') NOT LIKE '%SUPERSEDED%'
          AND ' ' + ISNULL(e.CURR_STATUS, '') + ' ' + ISNULL(e.REMARKS, '') + ' ' NOT LIKE '%[^A-Z]VOID[^A-Z]%'
    ), staged AS (
        SELECT s.*,
            -- last achieved stage (actual on or before the snapshot date)
            CASE WHEN s.A7 <= @SnapDate THEN 7 WHEN s.A6 <= @SnapDate THEN 6 WHEN s.A5 <= @SnapDate THEN 5
                 WHEN s.A4 <= @SnapDate THEN 4 WHEN s.A3 <= @SnapDate THEN 3 WHEN s.A2 <= @SnapDate THEN 2
                 WHEN s.A1 <= @SnapDate THEN 1 ELSE 0 END AS LastIdx,
            CASE WHEN s.A7 <= @SnapDate OR s.St IN ('AFC', 'AFX', 'ADH') OR (s.Rq <> '' AND s.St = s.Rq) THEN 1 ELSE 0 END AS IsComplete
        FROM src s
    ), nxt AS (
        SELECT g.*, n.NextDue
        FROM staged g
        OUTER APPLY (
            SELECT TOP 1 v.p AS NextDue
            FROM (VALUES (1, g.P1), (2, g.P2), (3, g.P3), (4, g.P4), (5, g.P5), (6, g.P6), (7, g.P7)) v(i, p)
            WHERE v.i > g.LastIdx AND v.p IS NOT NULL
            ORDER BY v.i
        ) n
    ), per AS (
        SELECT Project_No, Discipline,
            COUNT(*)                                                                          AS Docs,
            SUM(IsComplete)                                                                   AS Complete,
            SUM(1 - IsComplete)                                                               AS Pending,
            SUM(CASE WHEN IsComplete = 0 AND NextDue < @SnapDate THEN 1 ELSE 0 END)           AS Overdue,
            SUM(CASE WHEN IsComplete = 0 AND NextDue >= @SnapDate
                          AND NextDue <= DATEADD(DAY, 14, @SnapDate) THEN 1 ELSE 0 END)       AS Due14,
            SUM(CASE WHEN IsComplete = 0 AND LastIdx = 0 THEN 1 ELSE 0 END)                   AS NotStarted,
            SUM(Hours)                                                                        AS EstHours,
            SUM(Earned)                                                                       AS EarnedHours,
            SUM(Hours * ISNULL(PlnFrac, 0))                                                   AS PlannedHours
        FROM nxt
        GROUP BY GROUPING SETS ((Project_No, Discipline), (Project_No))
    ), m75 AS (
        SELECT Project_No, ISNULL(NULLIF(LTRIM(RTRIM(Discipline)), ''), '(Blank)') AS Discipline,
               SUM(ISNULL(AFC_PLN_TOTAL, 0)) AS M75Plan, SUM(ISNULL(AFCX_COUNT, 0) + ISNULL(ADH_COUNT, 0)) AS M75Done
        FROM dbo.SDDR_ACON_AFC_STATUS_01
        WHERE @Project IS NULL OR Project_No = @Project
        GROUP BY GROUPING SETS ((Project_No, ISNULL(NULLIF(LTRIM(RTRIM(Discipline)), ''), '(Blank)')), (Project_No))
    )
    SELECT per.Project_No, ISNULL(per.Discipline, '*') AS Discipline, per.Docs, per.Complete, per.Pending, per.Overdue,
           per.Due14, per.NotStarted, per.EstHours, per.EarnedHours, per.PlannedHours,
           ISNULL(m.M75Plan, 0) AS M75Plan, ISNULL(m.M75Done, 0) AS M75Done
    INTO #snap
    FROM per
    LEFT JOIN m75 m ON m.Project_No = per.Project_No AND ISNULL(m.Discipline, '*') = ISNULL(per.Discipline, '*');

    BEGIN TRAN;
        DELETE FROM dbo.SDDR_DASH_SNAPSHOT
        WHERE SnapDate = @SnapDate AND (@Project IS NULL OR Project_No = @Project);

        INSERT INTO dbo.SDDR_DASH_SNAPSHOT
            (SnapDate, Project_No, Discipline, Docs, Complete, Pending, Overdue, Due14, NotStarted,
             EstHours, EarnedHours, PlannedHours, M75Plan, M75Done)
        SELECT @SnapDate, Project_No, Discipline, Docs, Complete, Pending, Overdue, Due14, NotStarted,
               EstHours, EarnedHours, PlannedHours, M75Plan, M75Done
        FROM #snap;
    COMMIT;
END
GO

/* ------------------------------------------------------------------------------------
   Baseline
   ------------------------------------------------------------------------------------ */
CREATE OR ALTER PROCEDURE dbo.usp_SDDR_Dash_Baseline
    @Project varchar(40),
    @Name    varchar(60),
    @User    varchar(100) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @Project IS NULL OR LTRIM(@Project) = '' OR @Name IS NULL OR LTRIM(@Name) = ''
        THROW 50001, 'Project and baseline name are required.', 1;

    BEGIN TRAN;
        DELETE FROM dbo.SDDR_DASH_BASELINE WHERE Project_No = @Project AND BaselineName = @Name;

        ;WITH src AS (
            SELECT e.*,
                   COALESCE(NULLIF(LTRIM(RTRIM(CONVERT(varchar(50), e.DDR_ID))), ''), e.Document_No) AS DocKey,
                   ROW_NUMBER() OVER (PARTITION BY COALESCE(NULLIF(LTRIM(RTRIM(CONVERT(varchar(50), e.DDR_ID))), ''), e.Document_No)
                                      ORDER BY e.Document_No) AS rn
            FROM dbo.SDDR_ACON_DDR_EPR e
            WHERE e.Project_No = @Project AND e.Document_No IS NOT NULL
        )
        INSERT INTO dbo.SDDR_DASH_BASELINE
            (BaselineName, Project_No, DocKey, DDR_ID, Document_No,
             IDC_PLN, IFR_PLN, RCC_PLN, IFR2_PLN, RCC2_PLN, APP_PLN, AFC_PLN, CapturedAt, CapturedBy)
        SELECT @Name, @Project, LEFT(DocKey, 150), CONVERT(varchar(50), DDR_ID), LEFT(Document_No, 150),
               NULLIF(TRY_CONVERT(date, IDC_PLN),  '19000101'), NULLIF(TRY_CONVERT(date, IFR_PLN),  '19000101'),
               NULLIF(TRY_CONVERT(date, RCC_PLN),  '19000101'), NULLIF(TRY_CONVERT(date, IFR2_PLN), '19000101'),
               NULLIF(TRY_CONVERT(date, RCC2_PLN), '19000101'), NULLIF(TRY_CONVERT(date, APP_PLN),  '19000101'),
               NULLIF(TRY_CONVERT(date, AFC_PLN),  '19000101'), SYSDATETIME(), @User
        FROM src
        WHERE rn = 1 AND DocKey IS NOT NULL;
    COMMIT;
END
GO

/* ------------------------------------------------------------------------------------
   Permissions - grant the web application's database login what it needs
   (replace [SmartDDR_AppUser] with the login/user the site connects as).
   ------------------------------------------------------------------------------------ */
-- GRANT SELECT ON dbo.SDDR_DASH_SNAPSHOT TO [SmartDDR_AppUser];
-- GRANT SELECT ON dbo.SDDR_DASH_BASELINE TO [SmartDDR_AppUser];
-- GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.SDDR_DASH_VIEWS TO [SmartDDR_AppUser];
-- GRANT EXECUTE ON dbo.usp_SDDR_Dash_Snapshot TO [SmartDDR_AppUser];
-- GRANT EXECUTE ON dbo.usp_SDDR_Dash_Baseline TO [SmartDDR_AppUser];

/* ------------------------------------------------------------------------------------
   Optional: nightly snapshot job (SQL Server Agent). Needs rights in msdb.
   Remove the comment markers to create it; it runs every day at 02:00 server time.
   ------------------------------------------------------------------------------------ */
/*
USE msdb;
GO
IF EXISTS (SELECT 1 FROM msdb.dbo.sysjobs WHERE name = N'SmartDDR - nightly dashboard snapshot')
    EXEC msdb.dbo.sp_delete_job @job_name = N'SmartDDR - nightly dashboard snapshot';
EXEC msdb.dbo.sp_add_job        @job_name = N'SmartDDR - nightly dashboard snapshot', @enabled = 1;
EXEC msdb.dbo.sp_add_jobstep    @job_name = N'SmartDDR - nightly dashboard snapshot', @step_name = N'Snapshot',
                                @subsystem = N'TSQL', @database_name = N'ACAD_DATA',
                                @command = N'EXEC dbo.usp_SDDR_Dash_Snapshot;';
EXEC msdb.dbo.sp_add_schedule   @schedule_name = N'SmartDDR nightly 02:00', @freq_type = 4, @freq_interval = 1,
                                @active_start_time = 020000;
EXEC msdb.dbo.sp_attach_schedule @job_name = N'SmartDDR - nightly dashboard snapshot', @schedule_name = N'SmartDDR nightly 02:00';
EXEC msdb.dbo.sp_add_jobserver  @job_name = N'SmartDDR - nightly dashboard snapshot';
GO
*/
