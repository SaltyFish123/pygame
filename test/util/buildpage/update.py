################################################################################
# Imports

# StdLib
import sys
import os
import re
import cgi
import time
import glob
import shutil

# User Libs
import callproc
import config
import upload_results

from regexes import *
from helpers import *

################################################################################
# Results

BUILD_FAILED               = "Build FAILED, Tests not run"
BUILD_LINK_FAILED          = "Link FAILED, Tests not run"

BUILD_FAILED_EXCEPTION     = "Build FAILED, Exception"
BUILD_FAILED_UNKNOWN       = "Build FAILED, Unknown Errors"
BUILD_FAILED_UNPARSEABLE   = "Build FAILED, Unparseable Results"

TESTS_PASSED               = "Build Successful, Tests Passed"
TESTS_FAILED               = "Build Successful, Tests FAILED"
TESTS_INVALID              = "Build Successful, Invalid Test Results"

BUILD_SUCCESSFUL           = "Build Successful"

################################################################################
# Format Strings for errors

# Any errors with these keys will have the corresponding values html escaped
ESCAPE_KEYS = ('traceback', 'blame_line')

FILE_INFO = "%(error_file)s:%(line)s last rev: %(revision)s:%(user)s"

FORMATS = {

    TESTS_FAILED : FILE_INFO  + (
        '<br />%(test)s'
        '<br /><pre>%(traceback)s</pre>' ),

    BUILD_FAILED : FILE_INFO  + (
        '<br>ERROR: %(message)s' ),

    "BUILD_WARNINGS" : FILE_INFO + (
        '<br>warning:%(message)s'
        '<br><code>%(blame_line)s</code>'),
}

def errors_by_file_4_web(errors_by_file, format, cb = None, join='<hr>'):
    format_string = FORMATS[format]

    web_friendly = []

    for error_file, errors in errors_by_file.items():
        for error in errors:
            error.update({'error_file': os.path.basename(error_file)})
            if cb: cb(error)

            for k in ESCAPE_KEYS:
                if k in error: error[k] = cgi.escape(error[k])

            web_friendly.append(format_string % error)
    
    return join.join(web_friendly).replace('\n', '<br />')

################################################################################

def svn_blame(error_file):
    return callproc.GetReturnCodeAndOutput (
    ["svn", "blame", error_file], config.src_path, lineprintdiv = 100 )

def add_blame_to_errors_by_file( src_root, errors_by_file, line_func = None):
    if not line_func: line_func = lambda error: int(error['line'])
    
    for error_file, errors in errors_by_file.items():
        print "blame for %s" % error_file

        ret_code, blame_output = svn_blame(error_file)

        if ret_code is 0:
            blame_lines = blame_output.split('\n')

            for error in errors:
                line = line_func(error)
                blame = SVN_BLAME_RE.search(blame_lines[line - 1])
                error.update(blame.groupdict())

################################################################################

def categorize_errors_by_file(errors, add_blame = 1):
    errors_by_file = {}
    
    for error in errors:
        error_file = error['file']
        if error_file not in errors_by_file: errors_by_file[error_file] = []
        errors_by_file[error_file].append(error)
    
    if add_blame:
        add_blame_to_errors_by_file( config.src_path, errors_by_file )
    
    return errors_by_file

################################################################################

def build_warnings_html(build_output):
    warnings = [w.groupdict() for w in BUILD_WARNINGS_RE.finditer(build_output)]
    
    if warnings:
        warnings_by_file = categorize_errors_by_file(warnings)
        return errors_by_file_4_web (warnings_by_file, "BUILD_WARNINGS")

    return ""

################################################################################

def test_errors(output):
    errors = []
    for error in (e.groupdict() for e in ERROR_MATCHES_RE.finditer(output)):
        error.update(TRACEBACK_RE.search(error['traceback']).groupdict())
        errors.append(error)
    return errors

def parse_test_results(ret_code, output):
    failed_test = TESTS_FAILED_RE.search(output)
    errors = test_errors(output)

    if failed_test and errors:
        errors_by_file = categorize_errors_by_file(errors)
        web_friendly = errors_by_file_4_web(errors_by_file, TESTS_FAILED)

        return TESTS_FAILED, web_friendly

    elif ( (failed_test and not errors) or
           (ret_code is not 0 and not failed_test) ):

        return TESTS_INVALID, output.replace("\n", "<br>")
    
    else:
        tests_run = re.findall(r"loading ([^\r\n]+)", output)
        test_text = [test + " passed" for test in tests_run]

        return TESTS_PASSED, "<br>".join(test_text)

################################################################################

def parse_build_results(ret_code, output):
    # SUCCESS
    if ret_code is 0: return BUILD_SUCCESSFUL, ''

    # ERRORS
    errors = [e.groupdict() for e in BUILD_ERRORS_RE.finditer(output)]
    if errors:
        errors_by_file = categorize_errors_by_file (errors)
        web_friendly = errors_by_file_4_web(errors_by_file, BUILD_FAILED)
        return BUILD_FAILED, web_friendly

    # LINK ERRORS
    link_errors = [
        "%(source_name)s:%(message)s<br>" % s.groupdict()
        for s in LINK_ERRORS_RE.finditer(output)
    ]
    if link_errors: return BUILD_LINK_FAILED, ''.join(link_errors)

    # EXCEPTIONS 
    exceptions = BUILD_TRACEBACK_RE.search(output)
    if exceptions:
        errors = exceptions.groupdict()['traceback'].replace("\n", "<br>")
        return BUILD_FAILED_EXCEPTION, errors
    
    # UNKNOWN ERRORS
    error_matches = re.findall(r"^error: ([^\r\n]+)", output, re.MULTILINE)
    if error_matches:
        errors = ''.join(["%s<br>" % m for m in error_matches])
        return BUILD_FAILED_UNKNOWN, errors
    
    # ELSE
    return BUILD_FAILED_UNPARSEABLE, output.replace("\n", "<br>")

################################################################################

def configure_build():
    ret_code, output = callproc.InteractiveGetReturnCodeAndOutput (
        config.config_cmd, config.config_py_interaction, 
        config.src_path, config.build_env
    )
    if ret_code is not 0: sys.exit ("config.py error:\n%s" % output)

def build():
    return callproc.GetReturnCodeAndOutput (
        config.build_cmd, 
        config.src_path, config.build_env
    )

def install():
    return callproc.ExecuteAssertSuccess (
        config.install_cmd, 
        config.src_path, config.install_env
    )

def run_tests():
    return callproc.GetReturnCodeAndOutput (
        config.tests_cmd, config.src_path, config.test_env
    )

################################################################################

def upload_build_results( build_result, build_errors, build_warnings,
                          test_output, build_output ):
    write_file_lines (
        config.buildresults_filename,
        ( [config.latest_rev, time.strftime("%Y-%m-%d %H:%M"),
           build_result, build_errors, build_warnings] )
    )
    create_zip (
        config.buildresults_zip,
        
        *( config.buildresults_filename, 
           os.path.join(config.src_path, 'Setup')),

        **{ 'run_tests__output.txt'   :    test_output,
            'setup_py__output.txt'    :    build_output,
            'build_config.txt'        :    str(config) }
    )

    for results in (config.buildresults_filename, config.buildresults_zip):
        upload_results.scp(results)

    file(config.last_rev_filename, "w").write(str(config.latest_rev))

def upload_installer(build_result):
    installer_dist_path = glob.glob (
        os.path.join(config.dist_path, config.package_mask))[0]

    installer_filename = os.path.basename(installer_dist_path)

    if BUILD_SUCCESSFUL not in build_result:
        installer_filename = "failed_tests_%s" % installer_filename

    output_installer_path = os.path.join('./output', installer_filename)
    shutil.move(installer_dist_path, output_installer_path)

    build_info = [config.latest_rev, time.strftime("%Y-%m-%d %H:%M")]
    
    for upload in ("uploading", installer_filename):
        write_file_lines(config.prebuilts_filename, build_info + [upload])
        upload_results.scp(config.prebuilts_filename)

        if upload is "uploading":
            upload_results.scp(output_installer_path)

################################################################################

def prepare_build_env():
    os.chdir(os.path.dirname(__file__))

    if config.make_package:
        if os.path.exists(config.dist_path): cleardir(config.dist_path)

    prepare_dir(config.temp_install_path)
    os.makedirs(config.temp_install_pythonpath)
    
################################################################################

def update_build():
    configure_build()
    ret_code, build_output = build()

    build_result, build_errors = parse_build_results(ret_code, build_output)
    build_warnings = build_warnings_html(build_output)

    if build_result is BUILD_SUCCESSFUL:
        install()

        ret_code, test_output = run_tests()
        build_result, build_errors = parse_test_results(ret_code, test_output)

        upload_installer(build_result)

    print '\n%s\n' % build_result

    upload_build_results (
        build_result, build_errors, build_warnings, test_output, build_output
    )

################################################################################

def main():
    # All configuration done before hand in config.py. + ini files Treat config
    # as readonly from here on in.

    global config
    for config in config.get_configs(sys.argv[1:]):
        if 1 or config.previous_rev < config.latest_rev:
            prepare_build_env()
            update_build()

if __name__ == '__main__':
    main()

################################################################################