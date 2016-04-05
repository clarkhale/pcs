from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

from lxml.doctestcompare import LXMLOutputChecker
from lxml import etree
from doctest import Example

from pcs.lib.errors import LibraryError

class LibraryAssertionMixin(object):
    def __find_report_info(self, report_info_list, report_item):
        for report_info in report_info_list:
            if(
                report_item.severity == report_info[0]
                and
                report_item.code == report_info[1]
                and
                #checks only presence and match of expected in info,
                #extra info is ignored
                all(
                    (k in report_item.info and report_item.info[k]==v)
                    for k,v in report_info[2].items()
                )
            ):
                return report_info
        raise AssertionError(
            'Unexpected report given: {0}'
            .format(repr((
                report_item.severity, report_item.code, repr(report_item.info)
            )))
        )

    def assert_equal_report_item_list(
        self, real_report_item_list, report_info_list
    ):
        for report_item in real_report_item_list:
            report_info_list.remove(
                self.__find_report_info(report_info_list, report_item)
            )

        if report_info_list:
            raise AssertionError(
                'In the report from LibraryError was not present: '
                +', '+repr(report_info_list)
            )

    def assert_raise_library_error(self, callableObj, *report_info_list):
        if not report_info_list:
            raise AssertionError(
                'Raising LibraryError expected, but no report item specified.'
                +' Please specify report items, that you expect in LibraryError'
            )

        try:
            callableObj()
            raise AssertionError('LibraryError not raised')
        except LibraryError as e:
            self.assert_equal_report_item_list(e.args, list(report_info_list))

    def assert_cib_equal(self, expected_cib, got_cib=None):
        got_cib = got_cib if got_cib else self.cib
        got_xml = str(got_cib)
        expected_xml = str(expected_cib)
        assert_xml_equal(expected_xml, got_xml)


class XmlManipulation(object):
    @classmethod
    def from_file(cls, file_name):
        return cls(etree.parse(file_name).getroot())

    @classmethod
    def from_str(cls, string):
        return cls(etree.fromstring(string))

    def __init__(self, tree):
        self.tree = tree

    def __append_to_child(self, element, xml_string):
        element.append(etree.fromstring(xml_string))

    def append_to_first_tag_name(self, tag_name, *xml_string_list):
        for xml_string in xml_string_list:
            self.__append_to_child(
                self.tree.find(".//{0}".format(tag_name)), xml_string
            )
        return self

    def __str__(self):
        #etree returns string in bytes: b'xml'
        #python 3 removed .encode() from byte strings
        #run(...) calls subprocess.Popen.communicate which calls encode...
        #so there is bytes to str conversion
        return etree.tostring(self.tree).decode()


def get_xml_manipulation_creator_from_file(file_name):
    return lambda: XmlManipulation.from_file(file_name)


def assert_xml_equal(expected_xml, got_xml):
    checker = LXMLOutputChecker()
    if not checker.check_output(expected_xml, got_xml, 0):
        raise AssertionError(checker.output_difference(
            Example("", expected_xml),
            got_xml,
            0
        ))
