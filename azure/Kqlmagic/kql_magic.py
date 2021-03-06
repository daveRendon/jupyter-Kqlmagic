# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

import os
import time
import logging
import hashlib


from Kqlmagic.version import VERSION, get_pypi_latest_version, compare_version, execute_version_command, validate_required_python_version_running
from Kqlmagic.help import execute_usage_command, execute_help_command, execute_faq_command, UrlReference, MarkdownString
from Kqlmagic.constants import Constants

logging.getLogger(Constants.LOGGER_NAME).addHandler(logging.NullHandler())

from IPython.core.magic import Magics, magics_class, cell_magic, line_magic, needs_local_scope
from IPython.core.display import display
from IPython.core.magics.display import Javascript


from traitlets.config.configurable import Configurable
from traitlets import Bool, Int, Float, Unicode, Enum, TraitError, validate

from Kqlmagic.results import ResultSet
from Kqlmagic.connection import Connection

from Kqlmagic.parser import Parser
from Kqlmagic.parameterizer import Parameterizer

from Kqlmagic.log import Logger, logger, set_logger, create_log_context, set_logging_options
from Kqlmagic.display import Display
from Kqlmagic.database_html import Database_html
from Kqlmagic.help_html import Help_html
from Kqlmagic.kusto_engine import KustoEngine
from Kqlmagic.ai_engine import AppinsightsEngine
from Kqlmagic.la_engine import LoganalyticsEngine
from Kqlmagic.kql_engine import KqlEngineError
from Kqlmagic.palette import Palettes, Palette
from Kqlmagic.cache_engine import CacheEngine
from Kqlmagic.cache_client import CacheClient

_MAGIC_NAME = "kql"
_ENGINES = [KustoEngine, AppinsightsEngine, LoganalyticsEngine, CacheEngine]


@magics_class
class Kqlmagic(Magics, Configurable):
    """Runs KQL statement on a repository as specified by a connect string.

    Provides the %%kql magic."""

    # make sure the right python version is used
    validate_required_python_version_running(Constants.MINIMAL_PYTHON_VERSION_REQUIRED)

    auto_limit = Int(0, config=True, allow_none=True, help="Automatically limit the size of the returned result sets. Abbreviation: al")
    prettytable_style = Enum(
        ["DEFAULT", "MSWORD_FRIENDLY", "PLAIN_COLUMNS", "RANDOM"],
        "DEFAULT",
        config=True,
        help="Set the table printing style to any of prettytable's defined styles. Abbreviation: ptst",
    )
    short_errors = Bool(True, config=True, help="Don't display the full traceback on KQL Programming Error. Abbreviation: se")
    display_limit = Int(
        None,
        config=True,
        allow_none=True,
        help="Automatically limit the number of rows displayed (full result set is still stored). Abbreviation: dl",
    )
    auto_dataframe = Bool(False, config=True, help="Return Pandas dataframe instead of regular result sets. Abbreviation: ad")
    columns_to_local_vars = Bool(False, config=True, help="Return data into local variables from column names. Abbreviation: c2lv")
    feedback = Bool(True, config=True, help="Show number of records returned, and assigned variables. Abbreviation: f")
    show_conn_info = Enum(
        ["list", "current", "None"],
        "current",
        config=True,
        allow_none=True,
        help="Show connection info, either current, the whole list, or None. Abbreviation: sci",
    )
    dsn_filename = Unicode(
        "odbc.ini",
        config=True,
        help="Path to DSN file. "
        "When the first argument is of the form [section], "
        "a kql connection string is formed from the "
        "matching section in the DSN file. Abbreviation: dl",
    )
    timeout = Int(None, config=True, allow_none=True, help="Specifies the maximum time in seconds, to wait for a query response. None, means default http wait time. Abbreviation: to, wait")
    plot_package = Enum(["matplotlib", "plotly"], "plotly", config=True, help="Set the plot package. Abbreviation: pp")
    table_package = Enum(
        ["prettytable", "pandas", "plotly", "qgrid"], "prettytable", config=True, help="Set the table display package. Abbreviation: tp"
    )
    last_raw_result_var = Unicode(
        "_kql_raw_result_", config=True, help="Set the name of the variable that will contain last raw result. Abbreviation: var"
    )
    enable_suppress_result = Bool(True, config=True, help="Suppress result when magic ends with a semicolon ;. Abbreviation: esr")
    show_query_time = Bool(True, config=True, help="Print query execution elapsed time. Abbreviation: sqt")
    plotly_fs_includejs = Bool(
        False,
        config=True,
        help="Include plotly javascript code in popup window. If set to False (default), it download the script from https://cdn.plot.ly/plotly-latest.min.js. Abbreviation: pfi",
    )

    validate_connection_string = Bool(
        True, config=True, help="Validate connectionString with an implicit query, when query statement is missing. Abbreviation: vc"
    )
    auto_popup_schema = Bool(True, config=True, help="Popup schema when connecting to a new database. Abbreviation: aps")

    json_display = Enum(["raw", "native", "formatted"], "formatted", config=True, help="Set json/dict display format. Abbreviation: jd")
    palette_name = Unicode(Palettes.DEFAULT_NAME, config=True, help="Set pallete by name to be used for charts. Abbreviation: pn")
    palette_colors = Int(Palettes.DEFAULT_N_COLORS, config=True, help="Set pallete number of colors to be used for charts. Abbreviation: pc")
    palette_desaturation = Float(Palettes.DEFAULT_DESATURATION, config=True, help="Set pallete desaturation to be used for charts. Abbreviation: pd")

    temp_folder_name = Unicode("{0}_temp_files".format(Constants.MAGIC_CLASS_NAME), config=True, help="Set the folder name for temporary files")
    export_folder_name = Unicode("{0}_exported_files".format(Constants.MAGIC_CLASS_NAME), config=True, help="Set the folder name  for exported files")
    cache_folder_name = Unicode("{0}_cache_files".format(Constants.MAGIC_CLASS_NAME), config=True, help="Set the folder name for cache files")

    # valid values: jupyterlab or jupyternotebook
    notebook_app = Enum(["jupyterlab", "jupyternotebook"], "jupyternotebook", config=True, help="Set notebook application used.")

    add_kql_ref_to_help = Bool(True, config=True, help="On {} load auto add kql reference to Help menu.".format(Constants.MAGIC_CLASS_NAME))
    add_schema_to_help = Bool(True, config=True, help="On connection to database@cluster add  schema to Help menu.")
    cache = Unicode(None, config=True, allow_none=True, help="Cache query results to the specified folder.")
    use_cache = Unicode(None, config=True, allow_none=True, help="Use cached query results from the specified folder, instead of executing the query.")

    @validate("palette_name")
    def _valid_value_palette_name(self, proposal):
        try:
            Palette.validate_palette_name(proposal["value"])
        except AttributeError as e:
            message = "The 'palette_name' trait of a {0} instance {1}".format(Constants.MAGIC_CLASS_NAME, str(e))
            raise TraitError(message)
        return proposal["value"]

    @validate("palette_desaturation")
    def _valid_value_palette_desaturation(self, proposal):
        try:
            Palette.validate_palette_desaturation(proposal["value"])
        except AttributeError as e:
            message = "The 'palette_desaturation' trait of a {0} instance {1}".format(Constants.MAGIC_CLASS_NAME, str(e))
            raise TraitError(message)
        return proposal["value"]

    @validate("palette_colors")
    def _valid_value_palette_color(self, proposal):
        try:
            Palette.validate_palette_colors(proposal["value"])
        except AttributeError as e:
            message = "The 'palette_color' trait of a {0} instance {1}".format(Constants.MAGIC_CLASS_NAME, str(e))
            raise TraitError(message)
        return proposal["value"]

    def execute_cache_command(self, cache_name:str) -> str:
        """ execute the cache command.
        command enables or disables caching, and returns a status string

        Returns
        -------
        str
            A string with the new cache name, if None caching is diabled
        """
        if cache_name is not None and cache_name != "None" and len(cache_name) > 0:
            self.cache = cache_name
            return MarkdownString("{0} caching to folder **{1}** was enabled.".format(Constants.MAGIC_PACKAGE_NAME, self.cache))
        else:
            self.cache = None
            return MarkdownString("{0} caching was disabled.".format(Constants.MAGIC_PACKAGE_NAME))

    def execute_use_cache_command(self, cache_name:str) -> str:
        """ execute the use_cache command.
        command enables or disables use of cache, and returns a status string

        Returns
        -------
        str
            A string with the cache name, if None cache is diabled
        """
        if cache_name is not None and cache_name != "None" and len(cache_name) > 0:
            self.cache = cache_name
            return MarkdownString("{0} cache in folder **{1}** was enabled.".format(Constants.MAGIC_PACKAGE_NAME, self.use_cache))
        else:
            self.cache = None
            return MarkdownString("{0} cache was disabled.".format(Constants.MAGIC_PACKAGE_NAME))

    # [KUSTO]
    # Driver          = Easysoft ODBC-SQL Server
    # Server          = my_machine\SQLEXPRESS
    # User            = my_domain\my_user
    # Password        = my_password
    # If the database you want to connect to is the default
    # for the SQL Server login, omit this attribute
    # Database        = Northwind

    # Object constructor
    def __init__(self, shell):
        # constants
        Configurable.__init__(self, config=shell.config)
        Magics.__init__(self, shell=shell)

        set_logger(Logger())
        ip = get_ipython()  # pylint: disable=E0602
        ip.magic("matplotlib inline")

        # Add ourself to the list of module configurable via %config
        self.shell.configurables.append(self)

        load_mode = _get_kql_magic_load_mode()

        # old_logo = 'data:image/png;base64,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'
        # olq_logo-blue_kql ="data:image/png;base64,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"

        logo = "data:image/png;base64,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"
        if load_mode != "silent":
            html_str = (
                """<html>
            <head>
            <style>
            .kql-magic-banner {
                display: flex; 
                background-color: #d9edf7;
            }
            .kql-magic-banner > div {
                margin: 10px; 
                padding: 20px; 
                color: #3a87ad; 
                font-size: 13px;
            }
            </style>
            </head>
            <body>
                <div class='kql-magic-banner'>
                    <div><img src='"""+logo+"""'></div>
                    <div>
                        <p>Kql Query Language, aka kql, is the query language for advanced analytics on Azure Monitor resources. The current supported data sources are 
                        Azure Data Explorer (Kusto), Log Analytics and Application Insights. To get more information execute '%kql --help "kql"'</p>
                        <p>   &bull; kql reference: Click on 'Help' tab > and Select 'kql referece' or execute '%kql --help "kql"'<br>
                          &bull; """
                + Constants.MAGIC_CLASS_NAME
                + """ configuarion: execute '%config """
                + Constants.MAGIC_CLASS_NAME
                + """'<br>
                          &bull; """
                + Constants.MAGIC_CLASS_NAME
                + """ usage: execute '%kql --usage'<br>
                    </div>
                </div>
            </body>
            </html>"""
            )
            Display.show_html(html_str)
            Display.showInfoMessage(
                """{0} package is updated frequently. Run '!pip install {1} --no-cache-dir --upgrade' to use the latest version.<br>{0} version: {2}, source: {3}""".format(
                    Constants.MAGIC_PACKAGE_NAME, Constants.MAGIC_PIP_REFERENCE_NAME, VERSION, Constants.MAGIC_SOURCE_REPOSITORY_NAME
                )
            )
            # <div><img src='https://az818438.vo.msecnd.net/icons/kusto.png'></div>

            try:
                pypi_version = get_pypi_latest_version(Constants.MAGIC_PACKAGE_NAME)
                if pypi_version and compare_version(pypi_version) > 0:
                    Display.showWarningMessage(
                        """You are using {0} version {1}, however version {2} is available. You should consider upgrading, execute '!pip install {0} --no-cache-dir --upgrade'.""".format(
                            Constants.MAGIC_PACKAGE_NAME, VERSION, pypi_version
                        )
                    )
            except:
                pass

        _override_default_configuration(ip, load_mode)
        root_path = os.path.normpath(ip.starting_dir)

        folder_name = ip.run_line_magic("config", "{0}.temp_folder_name".format(Constants.MAGIC_CLASS_NAME))
        showfiles_folder_Full_name = root_path + "/" + folder_name
        if not os.path.exists(showfiles_folder_Full_name):
            os.makedirs(showfiles_folder_Full_name)
        # ipython will removed folder at shutdown or by restart
        ip.tempdirs.append(showfiles_folder_Full_name)
        Display.showfiles_base_path = root_path
        Display.showfiles_folder_name = folder_name
        Display.notebooks_host = Help_html.notebooks_host = os.getenv("AZURE_NOTEBOOKS_HOST")

        app = ip.run_line_magic("config", "{0}.notebook_app".format(Constants.MAGIC_CLASS_NAME))
        # add help link
        add_kql_ref_to_help = ip.run_line_magic("config", "{0}.add_kql_ref_to_help".format(Constants.MAGIC_CLASS_NAME))
        if add_kql_ref_to_help:
            Help_html.add_menu_item("kql Reference", "http://aka.ms/kdocs", notebook_app=app)
        if app is None or app != "jupyterlab":
            display(Javascript("""try {IPython.notebook.kernel.execute("NOTEBOOK_URL = '" + window.location + "'");} catch(err) {;}"""))
            time.sleep(5)
        _set_default_connections()

    @needs_local_scope
    @line_magic(Constants.MAGIC_NAME)
    @cell_magic(Constants.MAGIC_NAME)
    def execute(self, line, cell="", local_ns={}):
        """Query Kusto or ApplicationInsights using kusto query language (kql). Repository specified by a connect string.

        Magic Syntax::

            %%kql <connection-string>
            <KQL statement>
            # Note: establish connection and query.

            %%kql <established-connection-reference>
            <KQL statemnt>
            # Note: query using an established connection.

            %%kql
            <KQL statement>
            # Note: query using current established connection.

            %kql <KQL statment>
            # Note: single line query using current established connection.

            %kql <connection-string>
            # Note: established connection only.


        Connection string Syntax::

            kusto://username('<username>).password(<password>).cluster(<cluster>).database(<database>')

            appinsights://appid(<appid>).appkey(<appkey>)

            loganalytics://workspace(<workspaceid>).appkey(<appkey>)

            %<connectionStringVariable>%
            # Note: connection string is taken from the environment variable.

            [<sectionName>]
            # Note: connection string is built from the dsn file settings, section <sectionName>. 
            #       The dsn filename value is taken from configuartion value Kqlmagic.dsn_filename.

            # Note: if password or appkey component is missing, user will be prompted.
            # Note: connection string doesn't have to include all components, see examples below.
            # Note: substring of the form $name or ${name} in windows also %name%, are replaced by environment variables if exist.


        Examples::

            %%kql kusto://username('myName').password('myPassword').cluster('myCluster').database('myDatabase')
            <KQL statement>
            # Note: establish connection to Azure Data Explorer (kusto) and submit query.

            %%kql myDatabase@myCluster
            <KQL statement>
            # Note: submit query using using an established kusto connection to myDatabase database at cluster myCluster.

            %%kql appinsights://appid('myAppid').appkey('myAppkey')
            <KQL statement>
            # Note: establish connection to ApplicationInsights and submit query.

            %%kql myAppid@appinsights
            <KQL statement>
            # Note: submit query using established ApplicationInsights connection to myAppid.

            %%kql loganalytics://workspace('myWorkspaceid').appkey('myAppkey')
            <KQL statement>
            # Note: establish connection to LogAnalytics and submit query.

            %%kql myWorkspaceid@loganalytics
            <KQL statement>
            # Note: submit query using established LogAnalytics connection to myWorkspaceid.

            %%kql
            <KQL statement>
            # Note: submit query using current established connection.

            %kql <KQL statement>
            # Note: submit single line query using current established connection.

            %%kql kusto://cluster('myCluster').database('myDatabase')
            <KQL statement>
            # Note: establish connection to kusto using current username and password to form the full connection string and submit query.

            %%kql kusto://database('myDatabase')
            <KQL statement>
            # Note: establish connection to kusto using current username, password and cluster to form the full connection string and submit query.

            %kql kusto://username('myName').password('myPassword').cluster('myCluster')
            # Note: set current (default) username, passsword and cluster to kusto.

            %kql kusto://username('myName').password('myPassword')
            # Note: set current (default) username and password to kusto.

            %kql kusto://cluster('myCluster')
            # Note set current (default) cluster to kusto.
        """

        set_logger(Logger(None, create_log_context()))

        # save globals and locals so they can be referenced in bind vars
        user_ns = self.shell.user_ns.copy()
        user_ns.update(local_ns)

        logger().debug("To Parsed: \n\rline: {}\n\rcell:\n\r{}".format(line, cell))
        try:
            parsed = None
            parsed_queries = Parser.parse("%s\n%s" % (line, cell), self, _ENGINES, user_ns)
            logger().debug("Parsed: {}".format(parsed_queries))
            result = None
            for parsed in parsed_queries:
                parsed["line"] = line
                parsed["cell"] = cell
                popup_text = None
                options = parsed["options"]
                command = parsed["command"].get("command")
                if command is None or command == "submit":
                    result = self.execute_query(parsed, user_ns)
                else:
                    param = parsed["command"].get("param")
                    if command == "version":
                        result = execute_version_command()
                    elif command == "usage":
                        result = execute_usage_command()
                    elif command == "faq":
                        result = execute_faq_command()
                    elif command == "help":
                        result = execute_help_command(param)
                    elif command == "cache":
                        result = self.execute_cache_command(param)
                    elif command == "use_cache":
                        result = self.execute_use_cache_command(param)
                    elif command == "palette":
                        result = Palette(
                            palette_name=options.get("palette_name", self.palette_name),
                            n_colors=options.get("palette_colors", self.palette_colors),
                            desaturation=options.get("palette_desaturation", self.palette_desaturation),
                            to_reverse=options.get("palette_reverse", False),
                        )
                        properties = [
                            options.get("palette_name", self.palette_name), 
                            str(options.get("palette_colors", self.palette_colors)),
                            str(options.get("palette_desaturation", self.palette_desaturation)),
                            str(options.get("palette_reverse", False)),
                        ]
                        param = hashlib.sha1(bytes("#".join(properties), "utf-8")).hexdigest()
                        pname = options.get("palette_name")
                        popup_text = pname if not pname.startswith("[") else "custom"

                    elif command == "palettes":
                        n_colors = options.get("palette_colors", self.palette_colors)
                        desaturation = options.get("palette_desaturation", self.palette_desaturation)
                        result = Palettes(n_colors=n_colors, desaturation=desaturation)
                        properties = [
                            str(options.get("palette_colors", self.palette_colors)),
                            str(options.get("palette_desaturation", self.palette_desaturation)),
                        ]
                        param = hashlib.sha1(bytes("#".join(properties), "utf-8")).hexdigest()
                        popup_text = ' '
                    else:
                        raise ValueError("command {0} not implemented".format(command))
                    if isinstance(result, UrlReference):
                        Display.show_window(result.name, result.url, result.button_text, onclick_visibility="visible")
                        return None
                    if options.get("popup_window"):
                        _repr_html_ = getattr(result, "_repr_html_", None)
                        if _repr_html_ is not None and callable(_repr_html_):
                            html_str = result._repr_html_()
                            if html_str is not None:
                                button_text = "popup {0} ".format(command)
                                file_name = "{0}_command".format(command)
                                if param is not None and isinstance(param, str) and len(param) > 0:
                                    file_name += "_{0}".format(str(param))
                                    popup_text = popup_text or str(param)
                                if popup_text:
                                    button_text += " {0}".format(popup_text)
                                file_path = Display._html_to_file_path(html_str, file_name, **options)
                                Display.show_window(file_name, file_path, button_text=button_text, onclick_visibility="visible")
                                return None
            return result
        except Exception as e:
            if parsed:
                if parsed["options"].get("short_errors", self.short_errors):
                    Display.showDangerMessage(str(e))
                    return None
            elif self.short_errors:
                Display.showDangerMessage(str(e))
                return None
            raise

    def _get_connection_info(self, **options):
        mode = options.get("show_conn_info", self.show_conn_info)
        if mode == "current":
            return Connection.get_connection_list_formatted()
        elif mode == "list":
            return [Connection.get_current_connection_formatted()]
        return []

    def _show_connection_info(self, **options):
        msg = self._get_connection_info(**options)
        if len(msg) > 0:
            Display.showInfoMessage(msg)

    def submit_get_notebook_url(self):
        if self.notebook_app != "jupyterlab":
            display(Javascript("""try {IPython.notebook.kernel.execute("NOTEBOOK_URL = '" + window.location + "'");} catch(err) {;}"""))

    def execute_query(self, parsed, user_ns: dict, result_set=None):
        if Help_html.showfiles_base_url is None:
            window_location = user_ns.get("NOTEBOOK_URL")
            if window_location is not None:
                Help_html.flush(window_location, notebook_app=self.notebook_app)
            else:
                self.submit_get_notebook_url()

        query = parsed["query"].strip()
        options = parsed["options"]
        suppress_results = options.get("suppress_results", False) and options.get("enable_suppress_result", self.enable_suppress_result)
        connection_string = parsed["connection"]

        if not query and not connection_string:
            return None

        try:
            #
            # set connection
            #
            conn = Connection.get_connection(connection_string, user_ns, **options)

        # parse error
        except KqlEngineError as e:
            if options.get("short_errors", self.short_errors):
                msg = "to get help on connection string formats, run: %kql --help 'conn'"
                Display.showDangerMessage(str(e))
                Display.showInfoMessage(msg)
                return None
            else:
                raise

        # parse error
        except ConnectionError as e:
            if options.get("short_errors", self.short_errors):
                Display.showDangerMessage(str(e))
                self._show_connection_info(show_conn_info="list")
                return None
            else:
                raise

        try:
            # validate connection
            if not conn.options.get("validate_connection_string_done") and options.get("validate_connection_string", self.validate_connection_string):
                retry_with_code = False
                try:
                    conn.validate(**options)
                    conn.set_validation_result(True)
                except Exception as e:
                    msg = str(e)
                    if msg.find("AADSTS50079") > 0 and msg.find("multi-factor authentication") > 0 and isinstance(conn, KustoEngine):
                        Display.showDangerMessage(str(e))
                        retry_with_code = True
                    else:
                        raise e

                if retry_with_code:
                    Display.showInfoMessage("replaced connection with code authentication")
                    database_name = conn.get_database()
                    cluster_name = conn.get_cluster()
                    uri_schema_name = conn._URI_SCHEMA_NAME
                    connection_string = "{0}://code().cluster('{1}').database('{2}')".format(uri_schema_name, cluster_name, database_name)
                    conn = Connection.get_connection(connection_string, user_ns, **options)
                    conn.validate(**options)
                    conn.set_validation_result(True)

            conn.options["validate_connection_string_done"] = True

            schema_file_path = None
            if options.get("popup_schema") or (
                not conn.options.get("auto_popup_schema_done") and options.get("auto_popup_schema", self.auto_popup_schema)
            ):
                schema_file_path = Database_html.get_schema_file_path(conn, **options)
                Database_html.popup_schema(schema_file_path, conn)

            conn.options["auto_popup_schema_done"] = True
            if not conn.options.get("add_schema_to_help_done") and options.get("add_schema_to_help"):
                schema_file_path = schema_file_path or Database_html.get_schema_file_path(conn, **options)
                Help_html.add_menu_item(conn.get_conn_name(), schema_file_path, **options)
                conn.options["add_schema_to_help_done"] = True

            if not query:
                #
                # If NO  kql query, just return the current connection
                #
                if not connection_string and Connection.connections and not suppress_results:
                    self._show_connection_info(**options)
                return None
            #
            # submit query
            #
            start_time = time.time()

            params_dict = options.get("params_dict") or user_ns
            parametrized_query = Parameterizer(params_dict).expand(query) if result_set is None else result_set.parametrized_query
            raw_query_result = conn.execute(parametrized_query, user_ns, **options)

            end_time = time.time()

            if options.get("save_as") is not None:
                save_as_file_path = CacheClient().save(
                    raw_query_result, conn.get_database(), conn.get_cluster(), parametrized_query, filepath=options.get("save_as"), **options
                )
            if options.get("save_to") is not None:
                save_as_file_path = CacheClient().save(
                    raw_query_result, conn.get_database(), conn.get_cluster(), parametrized_query, filefolder=options.get("save_to"), **options
                )
            #
            # model query results
            #
            if result_set is None:
                fork_table_id = 0
                saved_result = ResultSet(
                    raw_query_result, parametrized_query, fork_table_id=0, fork_table_resultSets={}, metadata={}, options=options
                )
                saved_result.metadata["magic"] = self
                saved_result.metadata["parsed"] = parsed
                saved_result.metadata["connection"] = conn.get_conn_name()
            else:
                fork_table_id = result_set.fork_table_id
                saved_result = result_set.fork_result(0)
                saved_result.feedback_info = []
                saved_result._update(raw_query_result)

            result = saved_result

            if not connection_string and Connection.connections:
                saved_result.metadata["conn_info"] = self._get_connection_info(**options)
            else:
                saved_result.metadata["conn_info"] = []

            saved_result.metadata["start_time"] = start_time
            saved_result.metadata["end_time"] = end_time

            if saved_result.is_partial_table and not suppress_results:
                Display.showWarningMessage("partial results, query had errors (see {0}.dataSetCompletion)".format(options.get("last_raw_result_var")))

            if options.get("feedback", self.feedback):
                minutes, seconds = divmod(end_time - start_time, 60)
                saved_result.feedback_info.append("Done ({:0>2}:{:06.3f}): {} records".format(int(minutes), seconds, saved_result.records_count))

            if options.get("columns_to_local_vars", self.columns_to_local_vars):
                # Instead of returning values, set variables directly in the
                # users namespace. Variable names given by column names

                if options.get("feedback", self.feedback):
                    saved_result.feedback_info.append("Returning raw data to local variables")

                self.shell.user_ns.update(saved_result.to_dict())
                result = None

            if options.get("auto_dataframe", self.auto_dataframe):
                if options.get("feedback", self.feedback):
                    saved_result.feedback_info.append("Returning data converted to pandas dataframe")
                result = saved_result.to_dataframe()

            if options.get("result_var") and result_set is None:
                result_var = options["result_var"]
                if options.get("feedback", self.feedback):
                    saved_result.feedback_info.append("Returning data to local variable {}".format(result_var))
                self.shell.user_ns.update({result_var: result if result is not None else saved_result})
                result = None

            if options.get("cache") is not None and options.get("cache") != options.get("use_cache"):
                file_path = CacheClient().save(raw_query_result, conn.get_database(), conn.get_cluster(), parametrized_query, **options)
                if options.get("feedback", self.feedback):
                    saved_result.feedback_info.append("query results cached")

            if options.get("save_as") is not None:
                if options.get("feedback", self.feedback):
                    saved_result.feedback_info.append("query results saved as {0}".format(save_as_file_path))
            if options.get("save_to") is not None:
                if options.get("feedback", self.feedback):
                    path = "/".join(save_as_file_path.split("/")[:-1])
                    saved_result.feedback_info.append("query results saved to {0}".format(path))

            saved_result.suppress_result = False
            saved_result.display_info = False
            if result is not None:
                if suppress_results:
                    saved_result.suppress_result = True
                elif options.get("auto_dataframe", self.auto_dataframe):
                    Display.showSuccessMessage(saved_result.feedback_info)
                else:
                    saved_result.display_info = True

            if result_set is None:
                saved_result._create_fork_results()
            else:
                saved_result._update_fork_results()

            # Return results into the default ipython _ variable
            self.shell.user_ns.update({options.get("last_raw_result_var", self.last_raw_result_var): saved_result})

            if result == saved_result:
                result = saved_result.fork_result(fork_table_id)
            return result

        except Exception as e:
            if not connection_string and Connection.connections and not suppress_results:
                # display list of all connections
                self._show_connection_info(**options)

            if options.get("short_errors", self.short_errors):
                Display.showDangerMessage(e)
                return None
            else:
                raise e


def _override_default_configuration(ip, load_mode):
    """override default {0} configuration from environment variable {1}_CONFIGURATION.
       the settings should be separated by a semicolon delimiter.
       for example:
       {1}_CONFIGURATION = 'auto_limit = 1000; auto_dataframe = True' """.format(
        Constants.MAGIC_CLASS_NAME, Constants.MAGIC_CLASS_NAME.upper()
    )

    kql_magic_configuration = os.getenv("{0}_CONFIGURATION".format(Constants.MAGIC_CLASS_NAME.upper()))
    if kql_magic_configuration:
        kql_magic_configuration = kql_magic_configuration.strip()
        if kql_magic_configuration.startswith("'") or kql_magic_configuration.startswith('"'):
            kql_magic_configuration = kql_magic_configuration[1:-1]

        pairs = kql_magic_configuration.split(";")
        for pair in pairs:
            if pair:
                ip.run_line_magic("config", "{0}.{1}".format(Constants.MAGIC_CLASS_NAME, pair.strip()))

    app = os.getenv("{0}_NOTEBOOK_APP".format(Constants.MAGIC_CLASS_NAME.upper()))
    if app is not None:
        lookup_key = app.lower().strip().strip("\"'").replace("_", "").replace("-", "").replace("/", "")
        app = {"jupyterlab": "jupyterlab", "jupyternotebook": "jupyternotebook", "lab": "jupyterlab", "notebook": "jupyternotebook"}.get(lookup_key)
        if app is not None:
            ip.run_line_magic("config", '{0}.notebook_app = "{1}"'.format(Constants.MAGIC_CLASS_NAME, app.strip()))


def _get_kql_magic_load_mode():
    load_mode = os.getenv("{0}_LOAD_MODE".format(Constants.MAGIC_CLASS_NAME.upper()))
    if load_mode:
        load_mode = load_mode.strip().lower().replace("_", "").replace("-", "")
        if load_mode.startswith("'") or load_mode.startswith('"'):
            load_mode = load_mode[1:-1].strip()
    return load_mode


def _set_default_connections():
    connection_str = os.getenv("{0}_CONNECTION_STR".format(Constants.MAGIC_CLASS_NAME.upper()))
    if connection_str:
        connection_str = connection_str.strip()
        if connection_str.startswith("'") or connection_str.startswith('"'):
            connection_str = connection_str[1:-1]
        
        try:
            conn = Connection(connection_str, {})
            # ip = get_ipython()  # pylint: disable=E0602
            # result = ip.run_line_magic(Constants.MAGIC_NAME, connection_str)
            # if conn and _get_kql_magic_load_mode() != "silent":
            #     print(conn)
        except Exception as err:
            print(err)

"""
FAQ

Can I suppress the output of the query?
Answer: Yes you can. Add a semicolumn character ; as the last character of the kql query

Can I submit multiple kql queries in the same cell?
Answer: Yes you can. If you use the line kql magic %kql each line will submit a query. If you use the cell kql magic %%kql you should separate each query by an empty line

Can I save the results of the kql query to a python variable?
Answer: Yes you can. Add a prefix to the query with the variable and '<<'. for example:
        var1 << T | where c > 100

How can I get programmaticaly the last raw results of the last submitted query?
Answer: The raw results of the last submitted query, are save in the object _kql_raw_result_
        (this is the name of the default variable, the variable name can be configured)

Can I submit a kql query that render to a chart?
Answer: Yes you can. The output cell (if not supressed) will show the chart that is specified in the render command

Can I plot the chart of the last query from python?
Answer: Yes you can, assuming the kql query contained a render command. Execute the chart method on the result. for example:
        _kql_raw_result_.show_chart()

Can I display the table of the last query from python?
Answer: Yes you can, assuming the kql query contained a render command. Execute the chart method on the result. for example:
        _kql_raw_result_.show_table()

Can I submit last query again from python?
Answer: Yes you can. Execute the submit method on the result. for example:
        _kql_raw_result_.submit()

Can I get programmaticaly the last query string?
Answer: Yes you can. Get it from the query property of the result. for example:
        _kql_raw_result_.query

Can I get programmaticaly the connection name used for last query?
Answer: Yes you can. Get it from the query property of the result. for example:
        _kql_raw_result_.connection

Can I get programmaticaly the timing metadata of last query?
Answer: Yes you can. Get it from the folowing query properties: start_time, end_time and elapsed_timespan. for example:
        _kql_raw_result_.start_time
        _kql_raw_result_.end_time
        _kql_raw_result_.elapsed_timespan

Can I convert programmaticaly the raw results to a dataframe?
Answer: Yes you can. Execute the to_dataframe method on the result. For example:
        _kql_raw_result_.to_dataframe()

Can I get the kql query results as a dataframe instead of raw data?
Answer: Yes you can. Set the kql magic configuration parameter auto_dataframe to true, and all subsequent queries
        will return a dataframe instead of raw data (_kql_raw_result_ will continue to hold the raw results). For example:
        %config {0}.auto_dataframe = True
        %kql var1 << T | where c > 100 // var1 will hold the dataframe

If I use {0}.auto_dataframe = True, How can I get programmaticaly the last dataframe results of the last submitted query?
Answer: Execute the to_dataframe method on the result. For example:
        _kql_raw_result_.to_dataframe()

If I use {0}.auto_dataframe = True, How can I get programmaticaly the last raw results of the last submitted query?
Answer: _kql_raw_result_ holds the raw results.

""".format(
    Constants.MAGIC_CLASS_NAME
)
