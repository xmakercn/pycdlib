# Copyright (C) 2015  Chris Lalancette <clalancette@gmail.com>

# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation;
# version 2.1 of the License.

# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.

# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA

import struct
import time
import bisect
import collections
import StringIO
import socket
import random
import os

import sendfile

# There are a number of specific ways that numerical data is stored in the
# ISO9660/Ecma-119 standard.  In the text these are reference by the section
# number they are stored in.  A brief synopsis:
#
# 7.1.1 - 8-bit number
# 7.2.3 - 16-bit number, stored first as little-endian then as big-endian (4 bytes total)
# 7.3.1 - 32-bit number, stored as little-endian
# 7.3.2 - 32-bit number ,stored as big-endian
# 7.3.3 - 32-bit number, stored first as little-endian then as big-endian (8 bytes total)

VOLUME_DESCRIPTOR_TYPE_BOOT_RECORD = 0
VOLUME_DESCRIPTOR_TYPE_PRIMARY = 1
VOLUME_DESCRIPTOR_TYPE_SUPPLEMENTARY = 2
VOLUME_DESCRIPTOR_TYPE_VOLUME_PARTITION = 3
VOLUME_DESCRIPTOR_TYPE_SET_TERMINATOR = 255

class PyIsoException(Exception):
    '''
    The custom Exception class for PyIso.
    '''
    def __init__(self, msg):
        Exception.__init__(self, msg)

class ISODate(object):
    '''
    An interface class for Ecma-119 dates.  This is here to ensure that both
    the VolumeDescriptorDate class and the DirectoryRecordDate class implement
    the same interface.
    '''
    def parse(self, datestr):
        '''
        The unimplemeted parse method for the parent class.  The child class
        is expected to implement this.

        Parameters:
         datestr - The date string to parse.
        Returns:
         Nothing.
        '''
        raise NotImplementedError("Parse not yet implemented")
    def record(self):
        '''
        The unimplemented record method for the parent class.  The child class
        is expected to implement this.

        Parameters:
         None.
        Returns:
         String representing this date.
        '''
        raise NotImplementedError("Record not yet implemented")
    def new(self, tm=None):
        '''
        The unimplemented new method for the parent class.  The child class
        is expected to implement this.

        Parameters:
         tm - struct_time object to base new VolumeDescriptorDate off of,
              or None for an empty VolumeDescriptorDate.
        Returns:
         Nothing.
        '''
        raise NotImplementedError("New not yet implemented")

class HeaderVolumeDescriptor(object):
    '''
    A parent class for Primary and Supplementary Volume Descriptors.  The two
    types of descriptors share much of the same functionality, so this is the
    parent class that both classes derive from.
    '''
    def __init__(self):
        self.initialized = False
        self.path_table_records = []

    def parse(self, vd, data_fp, extent_loc):
        '''
        The unimplemented parse method for the parent class.  The child class
        is expected to implement this.

        Parameters:
         vd - The string to parse.
         data_fp - The file descriptor to associate with the root directory
                   record of the volume descriptor.
         extent_loc - The extent location that this Header Volume Descriptor resides
                      in on the original ISO.
        Returns:
         Nothing.
        '''
        raise PyIsoException("Child class must implement parse")

    def new(self, flags, sys_ident, vol_ident, set_size, seqnum, log_block_size,
            vol_set_ident, pub_ident, preparer_ident, app_ident,
            copyright_file, abstract_file, bibli_file, vol_expire_date,
            app_use):
        '''
        The unimplemented new method for the parent class.  The child class is
        expected to implement this.

        Parameters:
         flags - Optional flags to set for the header.
         sys_ident - The system identification string to use on the new ISO.
         vol_ident - The volume identification string to use on the new ISO.
         set_size - The size of the set of ISOs this ISO is a part of.
         seqnum - The sequence number of the set of this ISO.
         log_block_size - The logical block size to use for the ISO.  While
                          ISO9660 technically supports sizes other than 2048
                          (the default), this almost certainly doesn't work.
         vol_set_ident - The volume set identification string to use on the
                         new ISO.
         pub_ident_str - The publisher identification string to use on the
                         new ISO.
         preparer_ident_str - The preparer identification string to use on the
                              new ISO.
         app_ident_str - The application identification string to use on the
                         new ISO.
         copyright_file - The name of a file at the root of the ISO to use as
                          the copyright file.
         abstract_file - The name of a file at the root of the ISO to use as the
                         abstract file.
         bibli_file - The name of a file at the root of the ISO to use as the
                      bibliographic file.
         vol_expire_date - The date that this ISO will expire at.
         app_use - Arbitrary data that the application can stuff into the
                   primary volume descriptor of this ISO.
        Returns:
         Nothing.
        '''
        raise PyIsoException("Child class must implement new")

    def path_table_size(self):
        '''
        A method to get the path table size of the Volume Descriptor.

        Parameters:
         None.
        Returns:
         Path table size in bytes.
        '''
        if not self.initialized:
            raise PyIsoException("This Volume Descriptor is not yet initialized")

        return self.path_tbl_size

    def add_path_table_record(self, ptr):
        '''
        A method to add a new path table record to the Volume Descriptor.

        Parameters:
         ptr - The new path table record object to add to the list of path
               table records.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This Volume Descriptor is not yet initialized")
        # We keep the list of children in sorted order, based on the __lt__
        # method of the PathTableRecord object.
        bisect.insort_left(self.path_table_records, ptr)

    def path_table_record_be_equal_to_le(self, le_index, be_record):
        '''
        A method to compare a little-endian path table record to its
        big-endian counterpart.  This is used to ensure that the ISO is sane.

        Parameters:
         le_index - The index of the little-endian path table record in this
                    object's path_table_records.
         be_record - The big-endian object to compare with the little-endian
                     object.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This Volume Descriptor is not yet initialized")

        le_record = self.path_table_records[le_index]
        if be_record.len_di != le_record.len_di or \
           be_record.xattr_length != le_record.xattr_length or \
           swab_32bit(be_record.extent_location) != le_record.extent_location or \
           swab_16bit(be_record.parent_directory_num) != le_record.parent_directory_num or \
           be_record.directory_identifier != le_record.directory_identifier:
            return False
        return True

    def set_ptr_dirrecord(self, dirrecord):
        '''
        A method to store a directory record that is associated with a path
        table record.  This will be used during extent reshuffling to update
        all of the path table records with the correct values from the directory
        records.  Note that a path table record is said to be associated with
        a directory record when the file identification of the two match.

        Parameters:
         dirrecord - The directory record object to associate with a path table
                     record with the same file identification.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This Volume Descriptor is not yet initialized")
        if dirrecord.is_root:
            ptr_index = 0
        else:
            ptr_index = self.find_ptr_index_matching_ident(dirrecord.file_ident)
        self.path_table_records[ptr_index].set_dirrecord(dirrecord)

    def find_ptr_index_matching_ident(self, child_ident):
        '''
        A method to find a path table record index that matches a particular
        filename.

        Parameters:
         child_ident - The name of the file to find.
        Returns:
         Path table record index corresponding to the filename.
        '''
        if not self.initialized:
            raise PyIsoException("This Volume Descriptor is not yet initialized")

        # This is equivalent to bisect.bisect_left() (and in fact the code is
        # modified from there).  However, we already overrode the __lt__ method
        # in PathTableRecord(), and we wanted our own comparison between two
        # strings, so we open-code it here.  Also note that the first entry in
        # self.path_table_records is always the root, and since we can't remove
        # the root we don't have to look at it.
        lo = 1
        hi = len(self.path_table_records)
        while lo < hi:
            mid = (lo + hi) // 2
            if ptr_lt(self.path_table_records[mid].directory_identifier, child_ident):
                lo = mid + 1
            else:
                hi = mid
        saved_ptr_index = lo

        if saved_ptr_index == len(self.path_table_records):
            raise PyIsoException("Could not find path table record!")

        return saved_ptr_index

    def add_to_space_size(self, addition_bytes):
        '''
        A method to add bytes to the space size tracked by this Volume
        Descriptor.

        Parameters:
         addition_bytes - The number of bytes to add to the space size.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This Volume Descriptor is not yet initialized")
        # The "addition" parameter is expected to be in bytes, but the space
        # size we track is in extents.  Round up to the next extent.
        self.space_size += ceiling_div(addition_bytes, self.log_block_size)

    def remove_from_space_size(self, removal_bytes):
        '''
        Remove bytes from the volume descriptor.

        Parameters:
         removal_bytes - The number of bytes to remove.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This Volume Descriptor is not yet initialized")
        # The "removal" parameter is expected to be in bytes, but the space
        # size we track is in extents.  Round up to the next extent.
        self.space_size -= ceiling_div(removal_bytes, self.log_block_size)

    def root_directory_record(self):
        '''
        A method to get a handle to this Volume Descriptor's root directory
        record.

        Parameters:
         None.
        Returns:
         DirectoryRecord object representing this Volume Descriptor's root
         directory record.
        '''
        if not self.initialized:
            raise PyIsoException("This Volume Descriptor is not yet initialized")

        return self.root_dir_record

    def logical_block_size(self):
        '''
        A method to get this Volume Descriptor's logical block size.

        Parameters:
         None.
        Returns:
         Size of this Volume Descriptor's logical block size in bytes.
        '''
        if not self.initialized:
            raise PyIsoException("This Volume Descriptor is not yet initialized")

        return self.log_block_size

    def add_entry(self, flen, ptr_size=0):
        '''
        Add the length of a new file to the volume descriptor.

        Parameters:
         flen - The length of the file to add.
         ptr_size - The length to add to the path table record.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This Volume Descriptor is not yet initialized")

        # First add to the path table size.
        self.path_tbl_size += ptr_size
        if (ceiling_div(self.path_tbl_size, 4096) * 2) > self.path_table_num_extents:
            # If we overflowed the path table size, then we need to update the
            # space size.  Since we always add two extents for the little and
            # two for the big, add four total extents.  The locations will be
            # fixed up during reshuffle_extents.
            self.add_to_space_size(4 * self.log_block_size)
            self.path_table_num_extents += 2

        # Now add to the space size.
        self.add_to_space_size(flen)

    def remove_entry(self, flen, directory_ident=None):
        '''
        Remove an entry from the volume descriptor.

        Parameters:
         flen - The number of bytes to remove.
         directory_ident - The identifier for the directory to remove.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This Volume Descriptor is not yet initialized")

        # First remove from our space size.
        self.remove_from_space_size(flen)

        if directory_ident != None:
            ptr_index = self.find_ptr_index_matching_ident(directory_ident)

            # Next remove from the Path Table Record size.
            self.path_tbl_size -= PathTableRecord.record_length(self.path_table_records[ptr_index].len_di)
            new_extents = ceiling_div(self.path_tbl_size, 4096) * 2

            if new_extents > self.path_table_num_extents:
                # This should never happen.
                raise PyIsoException("This should never happen")
            elif new_extents < self.path_table_num_extents:
                self.remove_from_space_size(4 * self.log_block_size)
                self.path_table_num_extents -= 2
            # implicit else, no work to do

            del self.path_table_records[ptr_index]

    def sequence_number(self):
        '''
        A method to get this Volume Descriptor's sequence number.

        Parameters:
         None.
        Returns:
         This Volume Descriptor's sequence number.
        '''
        if not self.initialized:
            raise PyIsoException("This Volume Descriptor is not yet initialized")

        return self.seqnum

    def find_parent_dirnum(self, parent):
        '''
        A method to find the directory number corresponding to the parent.

        Parameters:
         parent - The parent to find the directory number fo.
        Returns:
         An integer directory number corresponding to the parent.
        '''
        if not self.initialized:
            raise PyIsoException("This Volume Descriptor is not yet initialized")

        if parent.is_root:
            ptr_index = 0
        else:
            ptr_index = self.find_ptr_index_matching_ident(parent.file_ident)

        return self.path_table_records[ptr_index].directory_num

    def update_ptr_extent_locations(self):
        '''
        Walk the path table records, updating the extent locations for each one
        based on the directory record.  This is used after reassigning extents
        on the ISO so that the path table records will all be up-to-date.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This Volume Descriptor is not yet initialized")

        for ptr in self.path_table_records:
            ptr.update_extent_location_from_dirrecord()

class VolumeDescriptorDate(ISODate):
    '''
    A class to represent a Volume Descriptor Date as described in Ecma-119
    section 8.4.26.1.  The Volume Descriptor Date consists of a year (from 1 to
    9999), month (from 1 to 12), day of month (from 1 to 31), hour (from 0
    to 23), minute (from 0 to 59), second (from 0 to 59), hundredths of second,
    and offset from GMT in 15-minute intervals (from -48 to +52) fields.  There
    are two main ways to use this class: either to instantiate and then parse a
    string to fill in the fields (the parse() method), or to create a new entry
    with a tm structure (the new() method).
    '''
    def __init__(self):
        self.initialized = False
        self.time_fmt = "%Y%m%d%H%M%S"
        self.empty_string = '0'*16 + '\x00'

    def parse(self, datestr):
        '''
        Parse a Volume Descriptor Date out of a string.  A string of all zeros
        is valid, which means that the date in this field was not specified.

        Parameters:
          datestr - string to be parsed
        Returns:
          Nothing.
        '''
        if self.initialized:
            raise PyIsoException("This Volume Descriptor Date object is already initialized")

        if len(datestr) != 17:
            raise PyIsoException("Invalid ISO9660 date string")

        if datestr == self.empty_string or datestr == '\x00'*17 or datestr == '0'*17:
            # Ecma-119, 8.4.26.1 specifies that if the string was all the
            # digit zero, with the last byte 0, the time wasn't specified.
            # However, in practice I have found that some ISOs specify this
            # field as all the number 0, so we allow both.
            self.year = 0
            self.month = 0
            self.dayofmonth = 0
            self.hour = 0
            self.minute = 0
            self.second = 0
            self.hundredthsofsecond = 0
            self.gmtoffset = 0
            self.present = False
        else:
            timestruct = time.strptime(datestr[:-3], self.time_fmt)
            self.year = timestruct.tm_year
            self.month = timestruct.tm_mon
            self.dayofmonth = timestruct.tm_mday
            self.hour = timestruct.tm_hour
            self.minute = timestruct.tm_min
            self.second = timestruct.tm_sec
            self.hundredthsofsecond = int(datestr[14:15])
            self.gmtoffset, = struct.unpack("=b", datestr[16])
            self.present = True

        self.initialized = True
        self.date_str = datestr

    def record(self):
        '''
        Return the date string for this object.

        Parameters:
          None.
        Returns:
          Date as a string.
        '''
        if not self.initialized:
            raise PyIsoException("This Volume Descriptor Date is not yet initialized")

        return self.date_str

    def new(self, tm=None):
        '''
        Create a new Volume Descriptor Date.  If tm is None, then this Volume
        Descriptor Date will be full of zeros (meaning not specified).  If tm
        is not None, it is expected to be a struct_time object, at which point
        this Volume Descriptor Date object will be filled in with data from that
        struct_time.

        Parameters:
          tm - struct_time object to base new VolumeDescriptorDate off of,
               or None for an empty VolumeDescriptorDate.
        Returns:
          Nothing.
        '''
        if self.initialized:
            raise PyIsoException("This Volume Descriptor Date object is already initialized")

        if tm is not None:
            local = time.localtime(tm)
            self.year = local.tm_year
            self.month = local.tm_mon
            self.day_of_month = local.tm_mday
            self.hour = local.tm_hour
            self.minute = local.tm_min
            self.second = local.tm_sec
            self.hundredthsofsecond = 0
            self.gmtoffset = gmtoffset_from_tm(tm, local)
            self.date_str = time.strftime(self.time_fmt, local) + "{:0<2}".format(self.hundredthsofsecond) + struct.pack("=b", self.gmtoffset)
            self.present = True
        else:
            self.year = 0
            self.month = 0
            self.dayofmonth = 0
            self.hour = 0
            self.minute = 0
            self.second = 0
            self.hundredthsofsecond = 0
            self.gmtoffset = 0
            self.date_str = self.empty_string
            self.present = False

        self.initialized = True

class FileOrTextIdentifier(object):
    '''
    A class to represent a file or text identifier as specified in Ecma-119
    section 8.4.20 (Primary Volume Descriptor Publisher Identifier),
    section 8.4.21 (Primary Volume Descriptor Data Preparer Identifier),
    and section 8.4.22 (Primary Volume Descriptor Application Identifier).  This
    identifier can either be a text string or the name of a file.  If it is a
    file, then the first byte will be 0x5f, the file should exist in the root
    directory record, and the file should be ISO level 1 interchange compliant
    (no more than 8 characters for the name and 3 characters for the extension).
    There are two main ways to use this class: either to instantiate and then
    parse a string to fill in the fields (the parse() method), or to create a
    new entry with a text string and whether this is a filename or not (the
    new() method).
    '''
    def __init__(self):
        self.initialized = False

    def parse(self, ident_str, is_primary):
        '''
        Parse a file or text identifier out of a string.

        Parameters:
          ident_str  - The string to parse the file or text identifier from.
          is_primary - Boolean describing whether this identifier is part of the
                       primary volume descriptor.  If it is, and it describes
                       a file (not a free-form string), it must be in ISO
                       interchange level 1 (MS-DOS style 8.3 format).
        Returns:
          Nothing.
        '''
        if self.initialized:
            raise PyIsoException("This File or Text identifier is already initialized")
        self.text = ident_str
        # According to Ecma-119, 8.4.20, 8.4.21, and 8.4.22, if the first
        # byte is a 0x5f, then the rest of the field specifies a filename.
        # Ecma-119 is vague, but presumably if it is not a filename, then it
        # is an arbitrary text string.
        self.isfile = False
        if ident_str[0] == "\x5f":
            # If the identifier is in the PVD, Ecma-119 says that it must
            # specify a file at the root directory and the identifier must
            # be 8.3 (so interchange level 1).  If the identifier is in an SVD,
            # Ecma-119 places no restrictions on the length of the filename
            # (though it implicitly has to be less than 31 so it can fit in
            # a directory record).

            # First find the end of the filename, which should be a space.
            space_index = -1
            for index,val in enumerate(ident_str[1:]):
                if ident_str[index] == ' ':
                    # Once we see a space, we know we are at the end of the
                    # filename.
                    space_index = index
                    break

            if is_primary:
                if space_index == -1:
                    # Never found the end of the filename, throw an exception.
                    raise PyIsoException("Invalid filename for file identifier")

                interchange_level = 1
            else:
                if space_index == -1:
                    space_index = None
                interchange_level = 3

            self.filename = ident_str[1:space_index]
            check_iso9660_filename(self.filename, interchange_level)

            self.isfile = True
            self.text = ident_str[1:]

        self.initialized = True

    def new(self, text, isfile):
        '''
        Create a new file or text identifier.  If isfile is True, then this is
        expected to be the name of a file at the root directory (as specified
        in Ecma-119), and to conform to ISO interchange level 1 (for the PVD),
        or ISO interchange level 3 (for an SVD).

        Parameters:
          text   - The text to store into the identifier.
          isfile - Whether this identifier is free-form text, or refers to a
                   file.
        Returns:
          Nothing.
        '''
        if self.initialized:
            raise PyIsoException("This File or Text identifier is already initialized")

        if len(text) > 128:
            raise PyIsoException("Length of text must be <= 128")

        if isfile:
            # Note that we do not check for whether this file identifier is in
            # 8.3 format (a requirement for primary volume descriptors).  This
            # is because we don't want to expose this to an outside user of the
            # API, so instead we have the check_filename() method below that
            # we call to do the checking at a later time.
            self.text = "{:<127}".format(text)
            self.filename = text
        else:
            self.text = "{:<128}".format(text)

        self.isfile = isfile
        self.initialized = True

    def is_file(self):
        '''
        Return True if this is a file identifier, False otherwise.

        Parameters:
          None.
        Returns:
          True if this identifier is a file, False if it is a free-form string.
        '''
        if not self.initialized:
            raise PyIsoException("This File or Text identifier is not yet initialized")
        return self.isfile

    def is_text(self):
        '''
        Returns True if this is a text identifier, False otherwise.

        Parameters:
          None.
        Returns:
          True if this identifier is a free-form file, False if it is a file.
        '''
        if not self.initialized:
            raise PyIsoException("This File or Text identifier is not yet initialized")
        return not self.isfile

    def record(self):
        '''
        Returns the file or text identification string suitable for recording.

        Parameters:
          None.
        Returns:
          The text representing this identifier.
        '''
        if not self.initialized:
            raise PyIsoException("This File or Text identifier is not yet initialized")
        if self.isfile:
            return "\x5f" + self.text
        # implicitly a text identifier
        return self.text

    def check_filename(self, is_primary):
        '''
        Checks whether the identifier stored in this object is a file, and if
        so, checks whether this filename conforms to the rules for the correct
        interchange level.

        Parameters:
         is_primary - A boolean that should be True if this is the Primay Volume
                      Descriptor, False otherwise.  This controls whether the
                      rules for interchange level 1 or interchange level 3
                      should be followed.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This File or Text identifier is not yet initialized")

        if self.isfile:
            interchange_level = 1
            if not is_primary:
                interchange_level = 3
            check_iso9660_filename(self.filename, interchange_level)

class DirectoryRecordDate(ISODate):
    '''
    A class to represent a Directory Record date as described in Ecma-119
    section 9.1.5.  The Directory Record date consists of the number of years
    since 1900, the month, the day of the month, the hour, the minute, the
    second, and the offset from GMT in 15 minute intervals.  There are two main
    ways to use this class: either to instantiate and then parse a string to
    fill in the fields (the parse() method), or to create a new entry with a
    tm structure (the new() method).
    '''
    def __init__(self):
        self.initialized = False
        self.fmt = "=BBBBBBb"

    def parse(self, datestr):
        '''
        Parse a Directory Record date out of a string.

        Parameters:
         datestr - The string to parse the date out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("Directory Record Date already initialized")

        (self.years_since_1900, self.month, self.day_of_month, self.hour,
         self.minute, self.second,
         self.gmtoffset) = struct.unpack(self.fmt, datestr)

        self.initialized = True

    def new(self, tm=None):
        '''
        Create a new Directory Record date based on the current time.

        Parameters:
         tm - An optional argument that must be None
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("Directory Record Date already initialized")

        if tm is not None:
            raise PyIsoException("Directory Record Date does not support passing tm in")

        # This algorithm was ported from cdrkit, genisoimage.c:iso9660_date()
        tm = time.time()
        local = time.localtime(tm)
        self.years_since_1900 = local.tm_year - 1900
        self.month = local.tm_mon
        self.day_of_month = local.tm_mday
        self.hour = local.tm_hour
        self.minute = local.tm_min
        self.second = local.tm_sec
        self.gmtoffset = gmtoffset_from_tm(tm, local)
        self.initialized = True

    def record(self):
        '''
        Return a string representation of the Directory Record date.

        Parameters:
         None.
        Returns:
         A string representing this Directory Record Date.
        '''
        if not self.initialized:
            raise PyIsoException("Directory Record Date not initialized")

        return struct.pack(self.fmt, self.years_since_1900, self.month,
                           self.day_of_month, self.hour, self.minute,
                           self.second, self.gmtoffset)

SU_ENTRY_VERSION = 1

class RRSPRecord(object):
    '''
    A class that represents a Rock Ridge Sharing Protocol record.  This record
    indicates that the sharing protocol is in use, and how many bytes to skip
    prior to parsing a Rock Ridge entry out of a directory record.
    '''
    def __init__(self):
        self.initialized = False

    def parse(self, rrstr):
        '''
        Parse a Rock Ridge Sharing Protocol record out of a string.

        Parameters:
         rrstr - The string to parse the record out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("SP record already initialized!")

        (su_len, su_entry_version, check_byte1, check_byte2,
         self.bytes_to_skip) = struct.unpack("=BBBBB", rrstr[2:7])

        # We assume that the caller has already checked the su_entry_version,
        # so we don't bother.

        if su_len != RRSPRecord.length():
            raise PyIsoException("Invalid length on rock ridge extension")
        if check_byte1 != 0xbe or check_byte2 != 0xef:
            raise PyIsoException("Invalid check bytes on rock ridge extension")

        self.initialized = True

    def new(self):
        '''
        Create a new Rock Ridge Sharing Protocol record.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("SP record already initialized!")

        self.bytes_to_skip = 0
        self.initialized = True

    def record(self):
        '''
        Generate a string representing the Rock Ridge Sharing Protocol record.

        Parameters:
         None.
        Returns:
         String containing the Rock Ridge record.
        '''
        if not self.initialized:
            raise PyIsoException("SP record not yet initialized!")

        return 'SP' + struct.pack("=BBBBB", RRSPRecord.length(), SU_ENTRY_VERSION, 0xbe, 0xef, self.bytes_to_skip)

    @staticmethod
    def length():
        '''
        Static method to return the length of the Rock Ridge Sharing Protocol
        record.

        Parameters:
         None.
        Returns:
         The length of this record in bytes.
        '''
        return 7

class RRRRRecord(object):
    '''
    A class that represents a Rock Ridge Rock Ridge record.  This optional
    record indicates which other Rock Ridge fields are present.
    '''
    def __init__(self):
        self.rr_flags = None
        self.initialized = False

    def parse(self, rrstr):
        '''
        Parse a Rock Ridge Rock Ridge record out of a string.

        Parameters:
         rrstr - The string to parse the record out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("RR record already initialized!")

        (su_len, su_entry_version, self.rr_flags) = struct.unpack("=BBB",
                                                                  rrstr[2:5])

        # We assume that the caller has already checked the su_entry_version,
        # so we don't bother.

        if su_len != RRRRRecord.length():
            raise PyIsoException("Invalid length on rock ridge extension")

        self.initialized = True

    def new(self):
        '''
        Create a new Rock Ridge Rock Ridge record.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("RR record already initialized!")

        self.rr_flags = 0
        self.initialized = True

    def append_field(self, fieldname):
        '''
        Mark a field as present in the Rock Ridge records.

        Parameters:
         fieldname - The name of the field to mark as present; should be one
                     of "PX", "PN", "SL", "NM", "CL", "PL", "RE", or "TF".
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("RR record not yet initialized!")

        if fieldname == "PX":
            bit = 0
        elif fieldname == "PN":
            bit = 1
        elif fieldname == "SL":
            bit = 2
        elif fieldname == "NM":
            bit = 3
        elif fieldname == "CL":
            bit = 4
        elif fieldname == "PL":
            bit = 5
        elif fieldname == "RE":
            bit = 6
        elif fieldname == "TF":
            bit = 7
        else:
            raise PyIsoException("Unknown RR field name %s" % (fieldname))

        self.rr_flags |= (1 << bit)

    def record(self):
        '''
        Generate a string representing the Rock Ridge Rock Ridge record.

        Parameters:
         None.
        Returns:
         String containing the Rock Ridge record.
        '''
        if not self.initialized:
            raise PyIsoException("RR record not yet initialized!")

        return 'RR' + struct.pack("=BBB", RRRRRecord.length(), SU_ENTRY_VERSION, self.rr_flags)

    @staticmethod
    def length():
        '''
        Static method to return the length of the Rock Ridge Rock Ridge
        record.

        Parameters:
         None.
        Returns:
         The length of this record in bytes.
        '''
        return 5

class RRCERecord(object):
    '''
    A class that represents a Rock Ridge Continuation Entry record.  This
    record represents additional information that did not fit in the standard
    directory record.
    '''
    def __init__(self):
        self.continuation_entry = None
        self.initialized = False

    def parse(self, rrstr):
        '''
        Parse a Rock Ridge Continuation Entry record out of a string.

        Parameters:
         rrstr - The string to parse the record out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("CE record already initialized!")

        (su_len, su_entry_version, bl_cont_area_le, bl_cont_area_be,
         offset_cont_area_le, offset_cont_area_be,
         len_cont_area_le, len_cont_area_be) = struct.unpack("=BBLLLLLL", rrstr[2:28])

        # We assume that the caller has already checked the su_entry_version,
        # so we don't bother.

        if su_len != RRCERecord.length():
            raise PyIsoException("Invalid length on rock ridge extension")

        self.continuation_entry = RockRidgeContinuation()
        self.continuation_entry.orig_extent_loc = bl_cont_area_le
        self.continuation_entry.continue_offset = offset_cont_area_le
        self.continuation_entry.increment_length(len_cont_area_le)

        self.initialized = True

    def new(self):
        '''
        Create a new Rock Ridge Continuation Entry record.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("CE record already initialized!")

        self.continuation_entry = RockRidgeContinuation()

        self.initialized = True

    def record(self):
        '''
        Generate a string representing the Rock Ridge Continuation Entry record.

        Parameters:
         None.
        Returns:
         String containing the Rock Ridge record.
        '''
        if not self.initialized:
            raise PyIsoException("CE record not yet initialized!")

        loc = self.continuation_entry.extent_location()
        offset = self.continuation_entry.offset()
        cont_len = self.continuation_entry.length()

        return 'CE' + struct.pack("=BBLLLLLL", RRCERecord.length(),
                                  SU_ENTRY_VERSION, loc, swab_32bit(loc),
                                  offset, swab_32bit(offset),
                                  cont_len, swab_32bit(cont_len))

    @staticmethod
    def length():
        '''
        Static method to return the length of the Rock Ridge Continuation Entry
        record.

        Parameters:
         None.
        Returns:
         The length of this record in bytes.
        '''
        return 28

class RRPXRecord(object):
    '''
    A class that represents a Rock Ridge POSIX File Attributes record.  This
    record contains information about the POSIX file mode, file links,
    user ID, group ID, and serial number of a directory record.
    '''
    def __init__(self):
        self.posix_file_mode = None
        self.posix_file_links = None
        self.posix_user_id = None
        self.posix_group_id = None
        self.posix_serial_number = None

        self.initialized = False

    def parse(self, rrstr):
        '''
        Parse a Rock Ridge POSIX File Attributes record out of a string.

        Parameters:
         rrstr - The string to parse the record out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("PX record already initialized!")

        (su_len, su_entry_version) = struct.unpack("=BB", rrstr[2:4])

        # We assume that the caller has already checked the su_entry_version,
        # so we don't bother.

        # In Rock Ridge 1.09, the su_len here should be 36, while for
        # 1.12, the su_len here should be 44.
        if su_len == 36:
            (posix_file_mode_le, posix_file_mode_be,
             posix_file_links_le, posix_file_links_be,
             posix_file_user_id_le, posix_file_user_id_be,
             posix_file_group_id_le,
             posix_file_group_id_be) = struct.unpack("=LLLLLLLL",
                                                     rrstr[4:36])
            posix_file_serial_number_le = 0
        elif su_len == 44:
            (posix_file_mode_le, posix_file_mode_be,
             posix_file_links_le, posix_file_links_be,
             posix_file_user_id_le, posix_file_user_id_be,
             posix_file_group_id_le, posix_file_group_id_be,
             posix_file_serial_number_le,
             posix_file_serial_number_be) = struct.unpack("=LLLLLLLLLL",
                                                          rrstr[4:44])
        else:
            raise PyIsoException("Invalid length on rock ridge extension")

        self.posix_file_mode = posix_file_mode_le
        self.posix_file_links = posix_file_links_le
        self.posix_user_id = posix_file_user_id_le
        self.posix_group_id = posix_file_group_id_le
        self.posix_serial_number = posix_file_serial_number_le

        self.initialized = True

    def new(self, isdir, symlink_path):
        '''
        Create a new Rock Ridge POSIX File Attributes record.

        Parameters:
         isdir - Whether this new entry is a directory.
         symlink_path - A symlink_path; None if this is not a symlink.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("PX record already initialized!")

        if isdir:
            self.posix_file_mode = 040555
        elif symlink_path is not None:
            self.posix_file_mode = 0120555
        else:
            self.posix_file_mode = 0100444

        self.posix_file_links = 1
        self.posix_user_id = 0
        self.posix_group_id = 0
        self.posix_serial_number = 0

        self.initialized = True

    def record(self, rr_version="1.09"):
        '''
        Generate a string representing the Rock Ridge POSIX File Attributes
        record.

        Parameters:
         None.
        Returns:
         String containing the Rock Ridge record.
        '''
        if not self.initialized:
            raise PyIsoException("PX record not yet initialized!")

        ret = 'PX' + struct.pack("=BBLLLLLLLL", RRPXRecord.length(),
                                 SU_ENTRY_VERSION, self.posix_file_mode,
                                 swab_32bit(self.posix_file_mode),
                                 self.posix_file_links,
                                 swab_32bit(self.posix_file_links),
                                 self.posix_user_id,
                                 swab_32bit(self.posix_user_id),
                                 self.posix_group_id,
                                 swab_32bit(self.posix_group_id))
        if rr_version != "1.09":
            ret += struct.pack("=LL", self.posix_serial_number,
                               swab_32bit(self.posix_serial_number))

        return ret

    @staticmethod
    def length(rr_version="1.09"):
        '''
        Static method to return the length of the Rock Ridge POSIX File
        Attributes record.

        Parameters:
         rr_version - The version of Rock Ridge in use; must be "1.09" or "1.12".
        Returns:
         The length of this record in bytes.
        '''
        if rr_version == "1.09":
            return 36
        else:
            return 44

class RRERRecord(object):
    '''
    A class that represents a Rock Ridge Extensions Reference record.
    '''
    def __init__(self):
        self.ext_id = None
        self.ext_des = None
        self.ext_src = None
        self.initialized = False

    def parse(self, rrstr):
        '''
        Parse a Rock Ridge Extensions Reference record out of a string.

        Parameters:
         rrstr - The string to parse the record out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("ER record already initialized!")

        (su_len, su_entry_version, len_id, len_des, len_src,
         ext_ver) = struct.unpack("=BBBBBB", rrstr[2:8])

        # We assume that the caller has already checked the su_entry_version,
        # so we don't bother.

        tmp = 8
        self.ext_id = rrstr[tmp:tmp+len_id]
        tmp += len_id
        self.ext_des = ""
        if len_des > 0:
            self.ext_des = rrstr[tmp:tmp+len_des]
            tmp += len_des
        self.ext_src = rrstr[tmp:tmp+len_src]
        tmp += len_src

        self.initialized = True

    def new(self, ext_id, ext_des, ext_src):
        '''
        Create a new Rock Ridge Extensions Reference record.

        Parameters:
         ext_id - The extension identifier to use.
         ext_des - The extension descriptor to use.
         ext_src - The extension specification source to use.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("ER record already initialized!")

        self.ext_id = ext_id
        self.ext_des = ext_des
        self.ext_src = ext_src

        self.initialized = True

    def record(self):
        '''
        Generate a string representing the Rock Ridge Extensions Reference
        record.

        Parameters:
         None.
        Returns:
         String containing the Rock Ridge record.
        '''
        if not self.initialized:
            raise PyIsoException("ER record not yet initialized!")

        return 'ER' + struct.pack("=BBBBBB", RRERRecord.length(self.ext_id, self.ext_des, self.ext_src), SU_ENTRY_VERSION, len(self.ext_id), len(self.ext_des), len(self.ext_src), 1) + self.ext_id + self.ext_des + self.ext_src

    @staticmethod
    def length(ext_id, ext_des, ext_src):
        '''
        Static method to return the length of the Rock Ridge Extensions Reference
        record.

        Parameters:
         ext_id - The extension identifier to use.
         ext_des - The extension descriptor to use.
         ext_src - The extension specification source to use.
        Returns:
         The length of this record in bytes.
        '''
        return 8+len(ext_id)+len(ext_des)+len(ext_src)

class RRESRecord(object):
    '''
    A class that represents a Rock Ridge Extension Selector record.
    '''
    def __init__(self):
        self.extension_sequence = None
        self.initialized = False

    def parse(self, rrstr):
        '''
        Parse a Rock Ridge Extension Selector record out of a string.

        Parameters:
         rrstr - The string to parse the record out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("ES record already initialized!")

        # We assume that the caller has already checked the su_entry_version,
        # so we don't bother.

        (su_len, su_entry_version, self.extension_sequence) = struct.unpack("=BBB", rrstr[2:5])
        if su_len != RRESRecord.length():
            raise PyIsoException("Invalid length on rock ridge extension")

        self.initialized = True

    def record(self):
        '''
        Generate a string representing the Rock Ridge Extension Selector record.

        Parameters:
         None.
        Returns:
         String containing the Rock Ridge record.
        '''
        if not self.initialized:
            raise PyIsoException("ES record not yet initialized!")

        return 'ES' + struct.pack("=BBB", RRESRecord.length(), SU_ENTRY_VERSION, self.extension_sequence)

    # FIXME: we need to implement the new method

    @staticmethod
    def length():
        '''
        Static method to return the length of the Rock Ridge Extensions Selector
        record.

        Parameters:
         None.
        Returns:
         The length of this record in bytes.
        '''
        return 5

class RRPNRecord(object):
    '''
    A class that represents a Rock Ridge POSIX Device Number record.  This
    record represents a device major and minor special file.
    '''
    def __init__(self):
        self.dev_t_high = None
        self.dev_t_low = None
        self.initialized = False

    def parse(self, rrstr):
        '''
        Parse a Rock Ridge POSIX Device Number record out of a string.

        Parameters:
         rrstr - The string to parse the record out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("PN record already initialized!")

        (su_len, su_entry_version, dev_t_high_le, dev_t_high_be,
         dev_t_low_le, dev_t_low_be) = struct.unpack("=BBLLLL", rrstr[2:20])

        # We assume that the caller has already checked the su_entry_version,
        # so we don't bother.

        if su_len != RRPNRecord.length():
            raise PyIsoException("Invalid length on rock ridge extension")

        self.dev_t_high = dev_t_high_le
        self.dev_t_low = dev_t_low_le

        self.initialized = True

    def record(self):
        '''
        Generate a string representing the Rock Ridge POSIX Device Number
        record.

        Parameters:
         None.
        Returns:
         String containing the Rock Ridge record.
        '''
        if not self.initialized:
            raise PyIsoException("PN record not yet initialized!")

        return 'PN' + struct.pack("=BBLLLL", RRPNRecord.length(), SU_ENTRY_VERSION, self.dev_t_high, swab_32bit(self.dev_t_high), self.dev_t_low, swab_32bit(self.dev_t_low))

    # FIXME: we need to implement the new method

    @staticmethod
    def length():
        '''
        Static method to return the length of the Rock Ridge POSIX Device Number
        record.

        Parameters:
         None.
        Returns:
         The length of this record in bytes.
        '''
        return 20

class RRSLRecord(object):
    '''
    A class that represents a Rock Ridge Symbolic Link record.  This record
    represents some or all of a symbolic link.  For a symbolic link, Rock Ridge
    specifies that each component (part of path separated by /) be in a separate
    component entry, and individual components may be split across multiple
    Symbolic Link records.  This class takes care of all of those details.
    '''
    def __init__(self):
        self.symlink_components = []
        self.flags = 0
        self.initialized = False

    def parse(self, rrstr):
        '''
        Parse a Rock Ridge Symbolic Link record out of a string.

        Parameters:
         rrstr - The string to parse the record out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("SL record already initialized!")

        (su_len, su_entry_version, self.flags) = struct.unpack("=BBB", rrstr[2:5])

        # We assume that the caller has already checked the su_entry_version,
        # so we don't bother.

        cr_offset = 5
        name = ""
        data_len = su_len - 5
        while data_len > 0:
            (cr_flags, len_cp) = struct.unpack("=BB", rrstr[cr_offset:cr_offset+2])

            data_len -= 2
            cr_offset += 2

            if not cr_flags in [0, 1, 2, 4, 8]:
                raise PyIsoException("Invalid Rock Ridge symlink flags 0x%x" % (cr_flags))

            if (cr_flags & (1 << 1) or cr_flags & (1 << 2) or cr_flags &(1 << 3)) and len_cp != 0:
                raise PyIsoException("Rock Ridge symlinks to dot or dotdot should have zero length")

            if (cr_flags & (1 << 1) or cr_flags & (1 << 2) or cr_flags & (1 << 3)) and name != "":
                raise PyIsoException("Cannot have RockRidge symlink that is both a continuation and dot or dotdot")

            if cr_flags & (1 << 1):
                name += "."
            elif cr_flags & (1 << 2):
                name += ".."
            elif cr_flags & (1 << 3):
                name += "/"
            else:
                name += rrstr[cr_offset:cr_offset+len_cp]

            if not (cr_flags & (1 << 0)):
                self.symlink_components.append(name)
                name = ''

            cr_offset += len_cp
            data_len -= len_cp

        self.initialized = True

    def new(self, symlink_path=None):
        '''
        Create a new Rock Ridge Symbolic Link record.

        Parameters:
         symlink_path - An optional path for the symbolic link.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("SL record already initialized!")

        if symlink_path is not None:
            self.symlink_components = symlink_path.split('/')

        self.initialized = True

    def add_component(self, symlink_comp):
        '''
        Add a new component to this symlink record.

        Parameters:
         symlink_comp - The string to add to this symlink record.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("SL record not yet initialized!")

        if (self.current_length() + 2 + len(symlink_comp)) > 255:
            raise PyIsoException("Symlink would be longer than 255")

        self.symlink_components.append(symlink_comp)

    def current_length(self):
        '''
        Calculate the current length of this symlink record.

        Parameters:
         None.
        Returns:
         Length of this symlink record.
        '''
        if not self.initialized:
            raise PyIsoException("SL record not yet initialized!")

        return RRSLRecord.length(self.symlink_components)

    def __str__(self):
        if not self.initialized:
            raise PyIsoException("SL record not yet initialized!")

        ret = ""
        for comp in self.symlink_components:
            ret += comp
            if comp != '/':
                ret += '/'

        return ret[:-1]

    def record(self):
        '''
        Generate a string representing the Rock Ridge Symbolic Link record.

        Parameters:
         None.
        Returns:
         String containing the Rock Ridge record.
        '''
        if not self.initialized:
            raise PyIsoException("SL record not yet initialized!")

        ret = 'SL' + struct.pack("=BBB", RRSLRecord.length(self.symlink_components), SU_ENTRY_VERSION, self.flags)
        for comp in self.symlink_components:
            if comp == '.':
                ret += struct.pack("=BB", (1 << 1), 0)
            elif comp == "..":
                ret += struct.pack("=BB", (1 << 2), 0)
            elif comp == "/":
                ret += struct.pack("=BB", (1 << 3), 0)
            else:
                ret += struct.pack("=BB", 0, len(comp)) + comp

        return ret

    @staticmethod
    def component_length(symlink_component):
        '''
        Static method to compute the length of one symlink component.

        Parameters:
         symlink_component - String representing one symlink component.
        Returns:
         Length of symlink component plus overhead.
        '''
        length = 2
        if symlink_component not in ['.', '..', '/']:
            length += len(symlink_component)

        return length

    @staticmethod
    def length(symlink_components):
        '''
        Static method to return the length of the Rock Ridge Symbolic Link
        record.

        Parameters:
         symlink_components - A list containing a string for each of the
                              symbolic link components.
        Returns:
         The length of this record in bytes.
        '''
        length = 5
        for comp in symlink_components:
            length += RRSLRecord.component_length(comp)
        return length

class RRNMRecord(object):
    '''
    A class that represents a Rock Ridge Alternate Name record.
    '''
    def __init__(self):
        self.initialized = False
        self.posix_name_flags = None
        self.posix_name = ''

    def parse(self, rrstr):
        '''
        Parse a Rock Ridge Alternate Name record out of a string.

        Parameters:
         rrstr - The string to parse the record out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("NM record already initialized!")

        (su_len, su_entry_version, self.posix_name_flags) = struct.unpack("=BBB", rrstr[2:5])

        # We assume that the caller has already checked the su_entry_version,
        # so we don't bother.

        name_len = su_len - 5
        if (self.posix_name_flags & 0x7) not in [0, 1, 2, 4]:
            raise PyIsoException("Invalid Rock Ridge NM flags")

        if name_len != 0:
            if (self.posix_name_flags & (1 << 1)) or (self.posix_name_flags & (1 << 2)) or (self.posix_name_flags & (1 << 5)):
                raise PyIsoException("Invalid name in Rock Ridge NM entry (0x%x %d)" % (self.posix_name_flags, name_len))
            self.posix_name += rrstr[5:5+name_len]

        self.initialized = True

    def new(self, rr_name):
        '''
        Create a new Rock Ridge Alternate Name record.

        Parameters:
         rr_name - The name for the new record.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("NM record already initialized!")

        self.posix_name = rr_name
        self.posix_name_flags = 0

        self.initialized = True

    def record(self):
        '''
        Generate a string representing the Rock Ridge Alternate Name record.

        Parameters:
         None.
        Returns:
         String containing the Rock Ridge record.
        '''
        if not self.initialized:
            raise PyIsoException("NM record not yet initialized!")

        return 'NM' + struct.pack("=BBB", RRNMRecord.length(self.posix_name), SU_ENTRY_VERSION, self.posix_name_flags) + self.posix_name

    def set_continued(self):
        '''
        Mark this alternate name record as continued.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("NM record not yet initialized!")

        self.posix_name_flags |= (1 << 0)

    @staticmethod
    def length(rr_name):
        '''
        Static method to return the length of the Rock Ridge Alternate Name
        record.

        Parameters:
         rr_name - The name to use.
        Returns:
         The length of this record in bytes.
        '''
        return 5 + len(rr_name)

class RRCLRecord(object):
    '''
    A class that represents a Rock Ridge Child Link record.  This record
    represents the logical block where a deeply nested directory was relocated
    to.
    '''
    def __init__(self):
        self.child_log_block_num = None
        self.initialized = False

    def parse(self, rrstr):
        '''
        Parse a Rock Ridge Child Link record out of a string.

        Parameters:
         rrstr - The string to parse the record out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("CL record already initialized!")

        # We assume that the caller has already checked the su_entry_version,
        # so we don't bother.

        (su_len, su_entry_version, child_log_block_num_le, child_log_block_num_be) = struct.unpack("=BBLL", rrstr[2:12])
        if su_len != RRCLRecord.length():
            raise PyIsoException("Invalid length on rock ridge extension")

        if child_log_block_num_le != swab_32bit(child_log_block_num_be):
            raise PyIsoException("Little endian block num does not equal big endian; corrupt ISO")
        self.child_log_block_num = child_log_block_num_le

    def new(self):
        '''
        Create a new Rock Ridge Child Link record.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("CL record already initialized!")

        self.child_log_block_num = 0 # FIXME: this isn't right

        self.initialized = True

    def record(self):
        '''
        Generate a string representing the Rock Ridge Child Link record.

        Parameters:
         None.
        Returns:
         String containing the Rock Ridge record.
        '''
        if not self.initialized:
            raise PyIsoException("CL record not yet initialized!")

        return 'CL' + struct.pack("=BBLL", RRCLRecord.length(), SU_ENTRY_VERSION, self.child_log_block_num, swab_32bit(self.child_log_block_num))

    def set_log_block_num(self, bl):
        '''
        Set the logical block number for the child.

        Parameters:
         bl - Logical block number of the child.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("CL record not yet initialized!")

        self.child_log_block_num = bl

    @staticmethod
    def length():
        '''
        Static method to return the length of the Rock Ridge Child Link
        record.

        Parameters:
         None.
        Returns:
         The length of this record in bytes.
        '''
        return 12

class RRPLRecord(object):
    '''
    A class that represents a Rock Ridge Parent Link record.  This record
    represents the logical block where a deeply nested directory was located
    from.
    '''
    def __init__(self):
        self.parent_log_block_num = None
        self.initialized = False

    def parse(self, rrstr):
        '''
        Parse a Rock Ridge Parent Link record out of a string.

        Parameters:
         rrstr - The string to parse the record out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("PL record already initialized!")

        # We assume that the caller has already checked the su_entry_version,
        # so we don't bother.

        (su_len, su_entry_version, parent_log_block_num_le, parent_log_block_num_be) = struct.unpack("=BBLL", rrstr[2:12])
        if su_len != RRPLRecord.length():
            raise PyIsoException("Invalid length on rock ridge extension")
        if parent_log_block_num_le != swab_32bit(parent_log_block_num_be):
            raise PyIsoException("Little endian block num does not equal big endian; corrupt ISO")
        self.parent_log_block_num = parent_log_block_num_le

    def new(self):
        '''
        Generate a string representing the Rock Ridge Parent Link record.

        Parameters:
         None.
        Returns:
         String containing the Rock Ridge record.
        '''
        if self.initialized:
            raise PyIsoException("PL record already initialized!")

        self.parent_log_block_num = 0 # FIXME: this isn't right

        self.initialized = True

    def record(self):
        '''
        Generate a string representing the Rock Ridge Child Link record.

        Parameters:
         None.
        Returns:
         String containing the Rock Ridge record.
        '''
        if not self.initialized:
            raise PyIsoException("PL record not yet initialized!")

        return 'PL' + struct.pack("=BBLL", RRPLRecord.length(), SU_ENTRY_VERSION, self.parent_log_block_num, swab_32bit(self.parent_log_block_num))

    def set_log_block_num(self, bl):
        '''
        Set the logical block number for the parent.

        Parameters:
         bl - Logical block number of the parent.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("PL record not yet initialized!")

        self.parent_log_block_num = bl

    @staticmethod
    def length():
        '''
        Static method to return the length of the Rock Ridge Parent Link
        record.

        Parameters:
         None.
        Returns:
         The length of this record in bytes.
        '''
        return 12

class RRTFRecord(object):
    '''
    A class that represents a Rock Ridge Time Stamp record.  This record
    represents the creation timestamp, the access time timestamp, the
    modification time timestamp, the attribute change time timestamp, the
    backup time timestamp, the expiration time timestamp, and the effective time
    timestamp.  Each of the timestamps can be selectively enabled or disabled.
    Additionally, the timestamps can be configured to be Directory Record
    style timestamps (7 bytes) or Volume Descriptor style timestamps (17 bytes).
    '''
    def __init__(self):
        self.creation_time = None
        self.access_time = None
        self.modification_time = None
        self.attribute_change_time = None
        self.backup_time = None
        self.expiration_time = None
        self.effective_time = None
        self.time_flags = None
        self.initialized = False

    def parse(self, rrstr):
        '''
        Parse a Rock Ridge Time Stamp record out of a string.

        Parameters:
         rrstr - The string to parse the record out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("TF record already initialized!")

        # We assume that the caller has already checked the su_entry_version,
        # so we don't bother.

        (su_len, su_entry_version, self.time_flags,) = struct.unpack("=BBB", rrstr[2:5])
        if su_len < 5:
            raise PyIsoException("Not enough bytes in the TF record")

        tflen = 7
        datetype = DirectoryRecordDate
        if self.time_flags & (1 << 7):
            tflen = 17
            datetype = VolumeDescriptorDate
        tmp = 5
        if self.time_flags & (1 << 0):
            self.creation_time = datetype()
            self.creation_time.parse(rrstr[tmp:tmp+tflen])
            tmp += tflen
        if self.time_flags & (1 << 1):
            self.access_time = datetype()
            self.access_time.parse(rrstr[tmp:tmp+tflen])
            tmp += tflen
        if self.time_flags & (1 << 2):
            self.modification_time = datetype()
            self.modification_time.parse(rrstr[tmp:tmp+tflen])
            tmp += tflen
        if self.time_flags & (1 << 3):
            self.attribute_change_time = datetype()
            self.attribute_change_time.parse(rrstr[tmp:tmp+tflen])
            tmp += tflen
        if self.time_flags & (1 << 4):
            self.backup_time = datetype()
            self.backup_time.parse(rrstr[tmp:tmp+tflen])
            tmp += tflen
        if self.time_flags & (1 << 5):
            self.expiration_time = datetype()
            self.expiration_time.parse(rrstr[tmp:tmp+tflen])
            tmp += tflen
        if self.time_flags & (1 << 6):
            self.effective_time = datetype()
            self.effective_time.parse(rrstr[tmp:tmp+tflen])
            tmp += tflen

        self.initialized = True

    def new(self, time_flags):
        '''
        Create a new Rock Ridge Time Stamp record.

        Parameters:
         time_flags - The flags to use for this time stamp record.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("TF record already initialized!")

        self.time_flags = time_flags

        datetype = DirectoryRecordDate
        if self.time_flags & (1 << 7):
            datetype = VolumeDescriptorDate

        if self.time_flags & (1 << 0):
            self.creation_time = datetype()
            self.creation_time.new()
        if self.time_flags & (1 << 1):
            self.access_time = datetype()
            self.access_time.new()
        if self.time_flags & (1 << 2):
            self.modification_time = datetype()
            self.modification_time.new()
        if self.time_flags & (1 << 3):
            self.attribute_change_time = datetype()
            self.attribute_change_time.new()
        if self.time_flags & (1 << 4):
            self.backup_time = datetype()
            self.backup_time.new()
        if self.time_flags & (1 << 5):
            self.expiration_time = datetype()
            self.expiration_time.new()
        if self.time_flags & (1 << 6):
            self.effective_time = datetype()
            self.effective_time.new()

        self.initialized = True

    def record(self):
        '''
        Generate a string representing the Rock Ridge Time Stamp record.

        Parameters:
         None.
        Returns:
         String containing the Rock Ridge record.
        '''
        if not self.initialized:
            raise PyIsoException("TF record not yet initialized!")

        ret = 'TF' + struct.pack("=BBB", RRTFRecord.length(self.time_flags), SU_ENTRY_VERSION, self.time_flags)
        if self.creation_time is not None:
            ret += self.creation_time.record()
        if self.access_time is not None:
            ret += self.access_time.record()
        if self.modification_time is not None:
            ret += self.modification_time.record()
        if self.attribute_change_time is not None:
            ret += self.attribute_change_time.record()
        if self.backup_time is not None:
            ret += self.backup_time.record()
        if self.expiration_time is not None:
            ret += self.expiration_time.record()
        if self.effective_time is not None:
            ret += self.effective_time.record()

        return ret

    @staticmethod
    def length(time_flags):
        '''
        Static method to return the length of the Rock Ridge Time Stamp
        record.

        Parameters:
         time_flags - Integer representing the flags to use.
        Returns:
         The length of this record in bytes.
        '''
        tf_each_size = 7
        if time_flags & (1 << 7):
            tf_each_size = 17
        tf_num = 0
        for i in range(0, 7):
            if time_flags & (1 << i):
                tf_num += 1

        return 5 + tf_each_size*tf_num

class RRSFRecord(object):
    '''
    A class that represents a Rock Ridge Sparse File record.  This record
    represents the full file size of a sparsely-populated file.
    '''
    def __init__(self):
        self.initialized = False

    def parse(self, rrstr):
        '''
        Parse a Rock Ridge Sparse File record out of a string.

        Parameters:
         rrstr - The string to parse the record out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("SF record already initialized!")

        # We assume that the caller has already checked the su_entry_version,
        # so we don't bother.

        (su_len, su_entry_version, virtual_file_size_high_le,
         virtual_file_size_high_be, virtual_file_size_low_le,
         virtual_file_size_low_be, self.table_depth) = struct.unpack("=BBLLLLB", rrstr[2:21])
        if su_len != RRSFRecord.length():
            raise PyIsoException("Invalid length on rock ridge extension")

        self.virtual_file_size_high = virtual_file_size_high_le
        self.virtual_file_size_low = virtual_file_size_low_le

        self.initialized = True

    def record(self):
        '''
        Generate a string representing the Rock Ridge Sparse File record.

        Parameters:
         None.
        Returns:
         String containing the Rock Ridge record.
        '''
        if not self.initialized:
            raise PyIsoException("SF record not yet initialized!")

        return 'SF' + struct.pack("=BBLLLLB", RRSFRecord.length(), SU_ENTRY_VERSION, self.virtual_file_size_high, swab_32bit(self.virtual_file_size_high), self.virtual_file_size_low, swab_32bit(self.virtual_file_size_low), self.table_depth)

    # FIXME: we need to implement the new method

    @staticmethod
    def length():
        '''
        Static method to return the length of the Rock Ridge Sparse File
        record.

        Parameters:
         None.
        Returns:
         The length of this record in bytes.
        '''
        return 21

class RRRERecord(object):
    '''
    A class that represents a Rock Ridge Relocated Directory record.  This
    record is used to mark an entry as having been relocated because it was
    deeply nested.
    '''
    def __init__(self):
        self.initialized = False

    def parse(self, rrstr):
        '''
        Parse a Rock Ridge Relocated Directory record out of a string.

        Parameters:
         rrstr - The string to parse the record out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("RE record already initialized!")

        (su_len, su_entry_version) = struct.unpack("=BB", rrstr[2:4])

        # We assume that the caller has already checked the su_entry_version,
        # so we don't bother.

        if su_len != 4:
            raise PyIsoException("Invalid length on rock ridge extension")

        self.initialized = True

    def new(self):
        '''
        Create a new Rock Ridge Relocated Directory record.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("RE record already initialized!")

        self.initialized = True

    def record(self):
        '''
        Generate a string representing the Rock Ridge Relocated Directory
        record.

        Parameters:
         None.
        Returns:
         String containing the Rock Ridge record.
        '''
        if not self.initialized:
            raise PyIsoException("RE record not yet initialized")

        return 'RE' + struct.pack("=BB", RRRERecord.length(), SU_ENTRY_VERSION)

    @staticmethod
    def length():
        '''
        Static method to return the length of the Rock Ridge Relocated Directory
        record.

        Parameters:
         None.
        Returns:
         The length of this record in bytes.
        '''
        return 4

# This is the class that implements the Rock Ridge extensions for PyIso.  The
# Rock Ridge extensions are a set of extensions for embedding POSIX semantics
# on an ISO9660 filesystem.  Rock Ridge works by utilizing the "System Use"
# area of the directory record to store additional metadata about files.  This
# includes things like POSIX users, groups, ctime, mtime, atime, etc., as well
# as the ability to have directory structures deeper than 8 and filenames longer
# than 8.3.  Rock Ridge depends on the System Use and Sharing Protocol (SUSP),
# which defines some standards on how to use the System Area.
#
# A note about versions.  PyIso implements version 1.12 of SUSP.  It implements
# both version 1.09 and 1.12 of Rock Ridge itself.  This is slightly strange,
# but genisoimage (which is what pyiso compares itself against) implements 1.09,
# so we keep support for both.
class RockRidgeBase(object):
    '''
    A base class representing Rock Ridge entries; both RockRidge and
    RockRidgeContinuation inherit from this class.
    '''
    def __init__(self):
        self.sp_record = None
        self.rr_record = None
        self.ce_record = None
        self.px_record = None
        self.er_record = None
        self.es_record = None
        self.pn_record = None
        self.sl_records = []
        self.nm_record = None
        self.cl_record = None
        self.pl_record = None
        self.tf_record = None
        self.sf_record = None
        self.re_record = None
        self.initialized = False

    def _parse(self, record, bytes_to_skip, is_first_dir_record_of_root):
        '''
        Internal method to parse a rock ridge record.

        Parameters:
         record - The record to parse.
         bytes_to_skip - The number of bytes to skip at the beginning of the
                         record.
         is_first_dir_record_of_root - Whether this is the first directory
                                       record of the root directory record;
                                       certain Rock Ridge entries are only
                                       valid there.
        Returns:
         Nothing.
        '''
        self.bytes_to_skip = bytes_to_skip
        offset = 0 + bytes_to_skip
        left = len(record)
        while True:
            if left == 0:
                break
            elif left == 1:
                # There may be a padding byte on the end.
                if record[offset] != '\x00':
                    raise PyIsoException("Invalid pad byte")
                break
            elif left < 4:
                raise PyIsoException("Not enough bytes left in the System Use field")

            (rtype, su_len, su_entry_version) = struct.unpack("=2sBB", record[offset:offset+4])
            if su_entry_version != SU_ENTRY_VERSION:
                raise PyIsoException("Invalid RR version %d!" % su_entry_version)

            if rtype == 'SP':
                if left < 7 or not is_first_dir_record_of_root:
                    raise PyIsoException("Invalid SUSP SP record")

                # OK, this is the first Directory Record of the root
                # directory, which means we should check it for the SUSP/RR
                # extension, which is exactly 7 bytes and starts with 'SP'.

                self.sp_record = RRSPRecord()
                self.sp_record.parse(record[offset:])
            elif rtype == 'RR':
                self.rr_record = RRRRRecord()
                self.rr_record.parse(record[offset:])
            elif rtype == 'CE':
                self.ce_record = RRCERecord()
                self.ce_record.parse(record[offset:])
            elif rtype == 'PX':
                self.px_record = RRPXRecord()
                self.px_record.parse(record[offset:])
            elif rtype == 'PD':
                # no work to do here
                pass
            elif rtype == 'ST':
                if su_len != 4:
                    raise PyIsoException("Invalid length on rock ridge extension")
            elif rtype == 'ER':
                self.er_record = RRERRecord()
                self.er_record.parse(record[offset:])
            elif rtype == 'ES':
                self.es_record = RRESRecord()
                self.es_record.parse(record[offset:])
            elif rtype == 'PN':
                self.pn_record = RRPNRecord()
                self.pn_record.parse(record[offset:])
            elif rtype == 'SL':
                new_sl_record = RRSLRecord()
                new_sl_record.parse(record[offset:])
                self.sl_records.append(new_sl_record)
            elif rtype == 'NM':
                self.nm_record = RRNMRecord()
                self.nm_record.parse(record[offset:])
            elif rtype == 'CL':
                self.cl_record = RRCLRecord()
                self.cl_record.parse(record[offset:])
            elif rtype == 'PL':
                self.pl_record = RRPLRecord()
                self.pl_record.parse(record[offset:])
            elif rtype == 'RE':
                self.re_record = RRRERecord()
                self.re_record.parse(record[offset:])
            elif rtype == 'TF':
                self.tf_record = RRTFRecord()
                self.tf_record.parse(record[offset:])
            elif rtype == 'SF':
                self.sf_record = RRSFRecord()
                self.sf_record.parse(record[offset:])
            else:
                raise PyIsoException("Unknown SUSP record %s" % (hexdump(rtype)))
            offset += su_len
            left -= su_len

        self.su_entry_version = 1
        self.initialized = True

    def record(self):
        '''
        Return a string representing the Rock Ridge entry.

        Parameters:
         None.
        Returns:
         A string representing the Rock Ridge entry.
        '''
        if not self.initialized:
            raise PyIsoException("Rock Ridge extension not yet initialized")

        ret = ''
        if self.sp_record is not None:
            ret += self.sp_record.record()

        if self.rr_record is not None:
            ret += self.rr_record.record()

        if self.nm_record is not None:
            ret += self.nm_record.record()

        if self.px_record is not None:
            ret += self.px_record.record()

        for sl_record in self.sl_records:
            ret += sl_record.record()

        if self.tf_record is not None:
            ret += self.tf_record.record()

        if self.ce_record is not None:
            ret += self.ce_record.record()

        if self.er_record is not None:
            ret += self.er_record.record()

        return ret

class RockRidgeContinuation(RockRidgeBase):
    '''
    A class representing a Rock Ridge continuation entry (inherits from
    RockRigeBase).
    '''
    def __init__(self):
        RockRidgeBase.__init__(self)

        # The new extent location will be set by _reshuffle_extents().
        self.orig_extent_loc = None
        self.new_extent_loc = None

        # The offset will get updated during _reshuffle_extents().
        self.continue_offset = 0

        self.continue_length = 0

        self.su_entry_version = 1

        self.initialized = True

    def extent_location(self):
        '''
        Get the extent location of this Rock Ridge Continuation entry.

        Parameters:
         None.
        Returns:
         An integer extent location for this continuation entry.
        '''
        if self.new_extent_loc is None and self.orig_extent_loc is None:
            raise PyIsoException("No extent assigned to Rock Ridge Continuation!")

        if self.new_extent_loc is None:
            return self.orig_extent_loc
        return self.new_extent_loc

    def offset(self):
        '''
        Get the offset from the beginning of the extent for this Rock Ridge
        Continuation entry.

        Parameters:
         None.
        Returns:
         An integer representing the offset from the beginning of the extent.
        '''
        return self.continue_offset

    def length(self):
        '''
        Get the length of this continuation entry.

        Parameters:
         None.
        Returns:
         An integer representing the length of this continuation entry.
        '''
        return self.continue_length

    def increment_length(self, length):
        '''
        Add a certain amount to the length of this continuation entry.

        Parameters:
         length - The length to add to this continuation entry.
        Returns:
         Nothing.
        '''
        self.continue_length += length

    def parse(self, record, bytes_to_skip):
        '''
        Parse a Rock Ridge continuation entry out of a string.

        Parameters:
         record - The string to parse.
         bytes_to_skip - The number of bytes to skip before parsing.
        Returns:
         Nothing.
        '''
        self.new_extent_loc = None

        self._parse(record, bytes_to_skip, False)

class RockRidge(RockRidgeBase):
    '''
    A class representing a Rock Ridge entry.
    '''
    def parse(self, record, is_first_dir_record_of_root, bytes_to_skip):
        '''
        A method to parse a rock ridge record.

        Parameters:
         record - The record to parse.
         is_first_dir_record_of_root - Whether this is the first directory
                                       record of the root directory record;
                                       certain Rock Ridge entries are only
                                       valid there.
         bytes_to_skip - The number of bytes to skip at the beginning of the
                         record.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("Rock Ridge extension already initialized")

        self._parse(record, bytes_to_skip, is_first_dir_record_of_root)

    def new(self, is_first_dir_record_of_root, rr_name, isdir, symlink_path,
            rr_version, curr_dr_len):
        '''
        Create a new Rock Ridge record.

        Parameters:
         is_first_dir_record_of_root - Whether this is the first directory
                                       record of the root directory record;
                                       certain Rock Ridge entries are only
                                       valid there.
         rr_name - The alternate name for this Rock Ridge entry.
         isdir - Whether this Rock Ridge entry is for a directory or not.
         symlink_path - The path to the target of the symlink, or None if this
                        is not a symlink.
         rr_version - The version of Rock Ridge to use; must be "1.09"
                      or "1.12".
         curr_dr_len - The current length of the directory record; this is used
                       when figuring out whether a continuation entry is needed.
        Returns:
         The length of the directory record after the Rock Ridge extension has
         been added.
        '''
        if self.initialized:
            raise PyIsoException("Rock Ridge extension already initialized")

        if rr_version != "1.09" and rr_version != "1.12":
            raise PyIsoException("Only Rock Ridge versions 1.09 and 1.12 are implemented")

        ALLOWED_DR_SIZE = 254
        TF_FLAGS = 0x0e
        EXT_ID = "RRIP_1991A"
        EXT_DES = "THE ROCK RIDGE INTERCHANGE PROTOCOL PROVIDES SUPPORT FOR POSIX FILE SYSTEM SEMANTICS"
        EXT_SRC = "PLEASE CONTACT DISC PUBLISHER FOR SPECIFICATION SOURCE.  SEE PUBLISHER IDENTIFIER IN PRIMARY VOLUME DESCRIPTOR FOR CONTACT INFORMATION."

        class dr_len(object):
            '''
            An internal class to make the directory record length have the same
            interface as a continuation entry.
            '''
            def __init__(self, _length):
                self._length = _length

            def length(self):
                '''
                Get the length of the directory record.

                Parameters:
                 None.
                Returns:
                 An integer representing the length of the directory record.
                '''
                return self._length

            def increment_length(self, _length):
                '''
                Add a certain amount to the length of the directory record.

                Parameters:
                 length - The length to add to the directory record.
                Returns:
                 Nothing.
                '''
                self._length += _length

        self.su_entry_version = 1

        # First we calculate the total length that this RR extension will take.
        # If it fits into the current DirectoryRecord, we stuff it directly in
        # here, and we are done.  If not, we know we'll have to add a
        # continuation entry.
        tmp_dr_len = curr_dr_len

        if is_first_dir_record_of_root:
            tmp_dr_len += RRSPRecord.length()

        if rr_version == "1.09":
            tmp_dr_len += RRRRRecord.length()

        if rr_name is not None:
            tmp_dr_len += RRNMRecord.length(rr_name)

        tmp_dr_len += RRPXRecord.length()

        if symlink_path is not None:
            tmp_dr_len += RRSLRecord.length(symlink_path.split('/'))

        tmp_dr_len += RRTFRecord.length(TF_FLAGS)

        if is_first_dir_record_of_root:
            tmp_dr_len += RRERRecord.length(EXT_ID, EXT_DES, EXT_SRC)

        this_dr_len = dr_len(curr_dr_len)

        if tmp_dr_len > ALLOWED_DR_SIZE:
            self.ce_record = RRCERecord()
            self.ce_record.new()
            this_dr_len.increment_length(RRCERecord.length())

        # For SP record
        if is_first_dir_record_of_root:
            new_sp = RRSPRecord()
            new_sp.new()
            thislen = RRSPRecord.length()
            if this_dr_len.length() + thislen > ALLOWED_DR_SIZE:
                self.ce_record.continuation_entry.sp_record = new_sp
                self.ce_record.continuation_entry.increment_length(thislen)
            else:
                self.sp_record = new_sp
                this_dr_len.increment_length(thislen)

        # For RR record
        if rr_version == "1.09":
            new_rr = RRRRRecord()
            new_rr.new()
            thislen = RRRRRecord.length()
            if this_dr_len.length() + thislen > ALLOWED_DR_SIZE:
                self.ce_record.continuation_entry.rr_record = new_rr
                self.ce_record.continuation_entry.increment_length(thislen)
            else:
                self.rr_record = new_rr
                this_dr_len.increment_length(thislen)

        # For NM record
        if rr_name is not None:
            if this_dr_len.length() + RRNMRecord.length(rr_name) > ALLOWED_DR_SIZE:
                # The length we are putting in this object (as opposed to
                # the continuation entry) is the maximum, minus how much is
                # already in the DR, minus 5 for the NM metadata.
                # FIXME: if len_here is 0, we shouldn't bother with the local
                # NM record.
                # FIXME: if the name is 255, and we are near the end of a block,
                # the name could spill into a follow-on continuation block.
                len_here = ALLOWED_DR_SIZE - this_dr_len.length() - 5
                self.nm_record = RRNMRecord()
                self.nm_record.new(rr_name[:len_here])
                self.nm_record.set_continued()
                this_dr_len.increment_length(RRNMRecord.length(rr_name[:len_here]))

                self.ce_record.continuation_entry.nm_record = RRNMRecord()
                self.ce_record.continuation_entry.nm_record.new(rr_name[len_here:])
                self.ce_record.continuation_entry.increment_length(RRNMRecord.length(rr_name[len_here:]))
            else:
                self.nm_record = RRNMRecord()
                self.nm_record.new(rr_name)
                this_dr_len.increment_length(RRNMRecord.length(rr_name))

            if self.rr_record is not None:
                self.rr_record.append_field("NM")

        # For PX record
        new_px = RRPXRecord()
        new_px.new(isdir, symlink_path)
        thislen = RRPXRecord.length()
        if this_dr_len.length() + thislen > ALLOWED_DR_SIZE:
            self.ce_record.continuation_entry.px_record = new_px
            self.ce_record.continuation_entry.increment_length(thislen)
        else:
            self.px_record = new_px
            this_dr_len.increment_length(thislen)

        if self.rr_record is not None:
            self.rr_record.append_field("PX")

        # For SL record
        if symlink_path is not None:
            curr_sl = RRSLRecord()
            curr_sl.new()
            if this_dr_len.length() + 5 + 2 + 1 < ALLOWED_DR_SIZE:
                self.sl_records.append(curr_sl)
                meta_record_len = this_dr_len
            else:
                self.ce_record.continuation_entry.sl_records.append(curr_sl)
                meta_record_len = self.ce_record.continuation_entry

            meta_record_len.increment_length(5)

            for comp in symlink_path.split('/'):
                if curr_sl.current_length() + 2 + len(comp) < 255:
                    # OK, this entire component fits into this symlink record,
                    # so add it.
                    curr_sl.add_component(comp)
                    meta_record_len.increment_length(RRSLRecord.component_length(comp))
                elif curr_sl.current_length() + 2 + 1 < 255:
                    # OK, at least part of this component fits into this symlink
                    # record, so add it, then add another one.
                    len_here = 255 - curr_sl.current_length() - 2
                    curr_sl.add_component(comp[:len_here])
                    meta_record_len.increment_length(RRSLRecord.component_length(comp[:len_here]))

                    curr_sl = RRSLRecord()
                    curr_sl.new(comp[len_here:])
                    self.ce_record.continuation_entry.sl_records.append(curr_sl)
                    meta_record_len = self.ce_record.continuation_entry
                    meta_record_len.increment_length(5 + RRSLRecord.component_length(comp[len_here:]))
                else:
                    # None of the this component fits into this symlink record,
                    # so add a continuation one.
                    curr_sl = RRSLRecord()
                    curr_sl.new(comp)
                    self.ce_record.continuation_entry.sl_records.append(curr_sl)
                    meta_record_len = self.ce_record.continuation_entry
                    meta_record_len.increment_length(5 + RRSLRecord.component_length(comp))

            if self.rr_record is not None:
                self.rr_record.append_field("SL")

        # For TF record
        new_tf = RRTFRecord()
        new_tf.new(TF_FLAGS)
        thislen = RRTFRecord.length(TF_FLAGS)
        if this_dr_len.length() + thislen > ALLOWED_DR_SIZE:
            self.ce_record.continuation_entry.tf_record = new_tf
            self.ce_record.continuation_entry.increment_length(thislen)
        else:
            self.tf_record = new_tf
            this_dr_len.increment_length(thislen)

        if self.rr_record is not None:
            self.rr_record.append_field("TF")

        # For ER record
        if is_first_dir_record_of_root:
            new_er = RRERRecord()
            new_er.new(EXT_ID, EXT_DES, EXT_SRC)
            thislen = RRERRecord.length(EXT_ID, EXT_DES, EXT_SRC)
            if this_dr_len.length() + thislen > ALLOWED_DR_SIZE:
                self.ce_record.continuation_entry.er_record = new_er
                self.ce_record.continuation_entry.increment_length(thislen)
            else:
                self.er_record = new_er
                this_dr_len.increment_length(thislen)

        self.initialized = True

        this_dr_len.increment_length(this_dr_len.length() % 2)

        return this_dr_len.length()

    def add_to_file_links(self):
        '''
        Increment the number of POSIX file links on this entry by one.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("Rock Ridge extension not yet initialized")

        if self.px_record is None:
            if self.ce_record is None:
                raise PyIsoException("No Rock Ridge file links and no continuation entry")
            self.ce_record.continuation_entry.px_record.posix_file_links += 1
        else:
            self.px_record.posix_file_links += 1

    def remove_from_file_links(self):
        '''
        Decrement the number of POSIX file links on this entry by one.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("Rock Ridge extension not yet initialized")

        if self.px_record is None:
            if self.ce_record is None:
                raise PyIsoException("No Rock Ridge file links and no continuation entry")
            self.ce_record.continuation_entry.px_record.posix_file_links -= 1
        else:
            self.px_record.posix_file_links -= 1

    def copy_file_links(self, src):
        '''
        Copy the number of file links from the source Rock Ridge entry into
        this Rock Ridge entry.

        Parameters:
         src - The source Rock Ridge entry to copy from.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("Rock Ridge extension not yet initialized")

        # First, get the src data
        if src.px_record is None:
            if src.ce_record is None:
                raise PyIsoException("No Rock Ridge file links and no continuation entry")
            num_links = src.ce_record.continuation_entry.px_record.posix_file_links
        else:
            num_links = src.px_record.posix_file_links

        # Now apply it to this record.
        if self.px_record is None:
            if self.ce_record is None:
                raise PyIsoException("No Rock Ridge file links and no continuation entry")
            self.ce_record.continuation_entry.px_record.posix_file_links = num_links
        else:
            self.px_record.posix_file_links = num_links

    def name(self):
        '''
        Get the alternate name from this Rock Ridge entry.

        Parameters:
         None.
        Returns:
         The alternate name from this Rock Ridge entry.
        '''
        if not self.initialized:
            raise PyIsoException("Rock Ridge extension not yet initialized")

        ret = ""
        if self.nm_record is not None:
            ret += self.nm_record.posix_name
        if self.ce_record is not None and self.ce_record.continuation_entry.nm_record is not None:
            ret += self.ce_record.continuation_entry.nm_record.posix_name

        return ret

    def is_symlink(self):
        '''
        Determine whether this Rock Ridge entry describes a symlink.

        Parameters:
         None.
        Returns:
         True if this Rock Ridge entry describes a symlink, False otherwise.
        '''
        if not self.initialized:
            raise PyIsoException("Rock Ridge extension not yet initialized")

        if self.sl_records:
            return True

        if self.ce_record is not None and self.ce_record.continuation_entry.sl_records:
            return True

        return False

    def symlink_path(self):
        '''
        Get the path as a string of the symlink target of this Rock Ridge entry
        (if this is a symlink).

        Parameters:
         None.
        Returns:
         Symlink path as a string.
        '''
        if not self.initialized:
            raise PyIsoException("Rock Ridge extension not yet initialized")

        if not self.sl_records or (self.ce_record is not None and not self.ce_record.continuation_entry.sl_records):
            raise PyIsoException("Entry is not a symlink!")

        ret = ""
        for rec in self.sl_records:
            recstr = str(rec)
            ret += recstr
            if recstr != "/":
                ret += "/"

        if self.ce_record is not None:
            for rec in self.ce_record.continuation_entry.sl_records:
                recstr = str(rec)
                ret += recstr
                if recstr != "/":
                    ret += "/"

        return ret[:-1]

class DirectoryRecord(object):
    '''
    A class that represents an ISO9660 directory record.
    '''
    FILE_FLAG_EXISTENCE_BIT = 0
    FILE_FLAG_DIRECTORY_BIT = 1
    FILE_FLAG_ASSOCIATED_FILE_BIT = 2
    FILE_FLAG_RECORD_BIT = 3
    FILE_FLAG_PROTECTION_BIT = 4
    FILE_FLAG_MULTI_EXTENT_BIT = 7

    DATA_ON_ORIGINAL_ISO = 1
    DATA_IN_EXTERNAL_FP = 2

    def __init__(self):
        self.initialized = False
        self.fmt = "=BBLLLL7sBBBHHB"

    def parse(self, record, data_fp, parent, logical_block_size):
        '''
        Parse a directory record out of a string.

        Parameters:
         record - The string to parse for this record.
         data_fp - The file object to associate with this record.
         parent - The parent of this record.
         logical_block_size - The logical block size for the ISO.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("Directory Record already initialized")

        if len(record) > 255:
            # Since the length is supposed to be 8 bits, this should never
            # happen.
            raise PyIsoException("Directory record longer than 255 bytes!")

        (self.dr_len, self.xattr_len, extent_location_le, extent_location_be,
         data_length_le, data_length_be, dr_date, self.file_flags,
         self.file_unit_size, self.interleave_gap_size, seqnum_le, seqnum_be,
         self.len_fi) = struct.unpack(self.fmt, record[:33])

        # In theory we should have a check here that checks to make sure that
        # the length of the record we were passed in matches the data record
        # length.  However, we have seen ISOs in the wild where this is
        # incorrect, so we elide the check here.

        if extent_location_le != swab_32bit(extent_location_be):
            raise PyIsoException("Little-endian (%d) and big-endian (%d) extent location disagree" % (extent_location_le, swab_32bit(extent_location_be)))
        self.orig_extent_loc = extent_location_le
        self.new_extent_loc = None

        # Theoretically, we should check to make sure that the little endian
        # data length is the same as the big endian data length.  In practice,
        # though, we've seen ISOs where this is wrong.  Skip the check, and just
        # pick the little-endian as the "actual" size, and hope for the best.

        self.data_length = data_length_le

        if seqnum_le != swab_16bit(seqnum_be):
            raise PyIsoException("Little-endian and big-endian seqnum disagree")
        self.seqnum = seqnum_le

        self.date = DirectoryRecordDate()
        self.date.parse(dr_date)

        # OK, we've unpacked what we can from the beginning of the string.  Now
        # we have to use the len_fi to get the rest.

        self.curr_length = 0
        self.children = []
        self.is_root = False
        self.isdir = False
        self.parent = parent
        self.original_data_location = self.DATA_ON_ORIGINAL_ISO
        self.data_fp = data_fp

        self.rock_ridge = None

        if self.parent is None:
            self.is_root = True

            # A root directory entry should always be exactly 34 bytes.
            # However, we have seen ISOs in the wild that get this wrong, so we
            # elide a check for it.

            # A root directory entry should always have 0 as the identifier.
            if record[33] != '\x00':
                raise PyIsoException("Invalid root directory entry identifier")
            self.file_ident = record[33]
            self.isdir = True
        else:
            record_offset = 33
            self.file_ident = record[record_offset:record_offset + self.len_fi]
            record_offset += self.len_fi
            if self.file_flags & (1 << self.FILE_FLAG_DIRECTORY_BIT):
                self.isdir = True

            if self.len_fi % 2 == 0:
                record_offset += 1

            if len(record[record_offset:]) >= 2 and record[record_offset:record_offset+2] in ['SP', 'RR', 'CE', 'PX', 'ER', 'ES', 'PN', 'SL', 'NM', 'CL', 'PL', 'TF', 'SF', 'RE']:
                self.rock_ridge = RockRidge()
                is_first_dir_record_of_root = self.file_ident == '\x00' and parent.parent is None

                if is_first_dir_record_of_root:
                    bytes_to_skip = 0
                elif parent.parent is None:
                    bytes_to_skip = parent.children[0].rock_ridge.bytes_to_skip
                else:
                    bytes_to_skip = parent.rock_ridge.bytes_to_skip

                self.rock_ridge.parse(record[record_offset:],
                                      is_first_dir_record_of_root,
                                      bytes_to_skip)

        if self.xattr_len != 0:
            if self.file_flags & (1 << self.FILE_FLAG_RECORD_BIT):
                raise PyIsoException("Record Bit not allowed with Extended Attributes")
            if self.file_flags & (1 << self.FILE_FLAG_PROTECTION_BIT):
                raise PyIsoException("Protection Bit not allowed with Extended Attributes")

        self.initialized = True

        return self.rock_ridge != None

    def _new(self, mangledname, parent, seqnum, isdir, length, rock_ridge,
             rr_name, rr_symlink_target):
        '''
        Internal method to create a new Directory Record.

        Parameters:
         mangledname - The ISO9660 name for this directory record.
         parent - The parent of this directory record.
         seqnum - The sequence number to associate with this directory record.
         isdir - Whether this directory record represents a directory.
         length - The length of the data for this directory record.
         rock_ridge - Whether this directory record should have a Rock Ridge
                      entry associated with it.
         rr_name - The Rock Ridge name to associate with this directory record.
         rr_symlink_target - The target for the symlink, if this is a symlink
                             record (otherwise, None).
        Returns:
         Nothing.
        '''

        # Adding a new time should really be done when we are going to write
        # the ISO (in record()).  Ecma-119 9.1.5 says:
        #
        # "This field shall indicate the date and the time of the day at which
        # the information in the Extent described by the Directory Record was
        # recorded."
        #
        # We create it here just to have something in the field, but we'll
        # redo the whole thing when we are mastering.
        self.date = DirectoryRecordDate()
        self.date.new()

        if length > 2**32-1:
            raise PyIsoException("Maximum supported file length is 2^32-1")

        self.data_length = length
        # FIXME: if the length of the item is more than 2^32 - 1, and the
        # interchange level is 3, we should make duplicate directory record
        # entries so we can represent the whole file (see
        # http://wiki.osdev.org/ISO_9660, Size Limitations for a discussion of
        # this).

        self.file_ident = mangledname

        self.isdir = isdir

        self.seqnum = seqnum
        # For a new directory record entry, there is no original_extent_loc,
        # so we leave it at None.
        self.orig_extent_loc = None
        self.len_fi = len(self.file_ident)
        self.dr_len = struct.calcsize(self.fmt) + self.len_fi

        # When adding a new directory, we always add a full extent.  This number
        # tracks how much of that block we are using so that we can figure out
        # if we need to allocate a new block.
        self.curr_length = 0

        # From Ecma-119, 9.1.6, the file flag bits are:
        #
        # Bit 0 - Existence - 0 for existence known, 1 for hidden
        # Bit 1 - Directory - 0 for file, 1 for directory
        # Bit 2 - Associated File - 0 for not associated, 1 for associated
        # Bit 3 - Record - 0 for structure not in xattr, 1 for structure in xattr
        # Bit 4 - Protection - 0 for no owner and group in xattr, 1 for owner and group in xattr
        # Bit 5 - Reserved
        # Bit 6 - Reserved
        # Bit 7 - Multi-extent - 0 for final directory record, 1 for not final directory record
        # FIXME: We probably want to allow the existence, associated file, xattr
        # record, and multi-extent bits to be set by the caller.
        self.file_flags = 0
        if self.isdir:
            self.file_flags |= (1 << self.FILE_FLAG_DIRECTORY_BIT)
        self.file_unit_size = 0 # FIXME: we don't support setting file unit size for now
        self.interleave_gap_size = 0 # FIXME: we don't support setting interleave gap size for now
        self.xattr_len = 0 # FIXME: we don't support xattrs for now
        self.children = []

        self.parent = parent
        self.is_root = False
        if parent is None:
            # If no parent, then this is the root
            self.is_root = True

        self.dr_len += (self.dr_len % 2)

        self.rock_ridge = None
        if rock_ridge:
            self.rock_ridge = RockRidge()
            is_first_dir_record_of_root = self.file_ident == '\x00' and parent.parent is None
            # FIXME: allow the user to set the rock ridge version
            self.dr_len = self.rock_ridge.new(is_first_dir_record_of_root,
                                              rr_name, self.isdir,
                                              rr_symlink_target, "1.09",
                                              self.dr_len)

            if self.isdir:
                if parent.parent is not None:
                    if self.file_ident == '\x00':
                        self.parent.rock_ridge.add_to_file_links()
                        self.rock_ridge.add_to_file_links()
                    elif self.file_ident == '\x01':
                        self.rock_ridge.copy_file_links(self.parent.parent.children[1].rock_ridge)
                    else:
                        self.parent.rock_ridge.add_to_file_links()
                        self.parent.children[0].rock_ridge.add_to_file_links()
                else:
                    if self.file_ident != '\x00' and self.file_ident != '\x01':
                        self.parent.children[0].rock_ridge.add_to_file_links()
                        self.parent.children[1].rock_ridge.add_to_file_links()
                    else:
                        self.rock_ridge.add_to_file_links()

        self.initialized = True

    def new_symlink(self, name, parent, rr_path, seqnum, rr_name):
        '''
        Create a new symlink Directory Record.  This implies that the new
        record will be Rock Ridge.

        Parameters:
         name - The name for this directory record.
         parent - The parent of this directory record.
         rr_path - The symlink target for this directory record.
         seqnum - The sequence number for this directory record.
         rr_name - The Rock Ridge name for this directory record.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("Directory Record already initialized")

        self._new(name, parent, seqnum, False, 0, True, rr_name, rr_path)

    def new_fp(self, fp, length, isoname, parent, seqnum, rock_ridge, rr_name):
        '''
        Create a new file Directory Record.

        Parameters:
         fp - A file object that contains the data for this directory record.
         length - The length of the data.
         isoname - The name for this directory record.
         parent - The parent of this directory record.
         seqnum - The sequence number for this directory record.
         rock_ridge - Whether to make this a Rock Ridge directory record.
         rr_name - The Rock Ridge name for this directory record.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("Directory Record already initialized")

        self.original_data_location = self.DATA_IN_EXTERNAL_FP
        self.data_fp = fp
        self._new(isoname, parent, seqnum, False, length, rock_ridge, rr_name, None)

    def new_root(self, seqnum, log_block_size):
        '''
        Create a new root Directory Record.

        Parameters:
         seqnum - The sequence number for this directory record.
         log_block_size - The logical block size to use.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("Directory Record already initialized")

        self._new('\x00', None, seqnum, True, log_block_size, False, None, None)

    def new_dot(self, root, seqnum, rock_ridge, log_block_size):
        '''
        Create a new "dot" Directory Record.

        Parameters:
         root - The parent of this directory record.
         seqnum - The sequence number for this directory record.
         rock_ridge - Whether to make this a Rock Ridge directory record.
         log_block_size - The logical block size to use.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("Directory Record already initialized")

        self._new('\x00', root, seqnum, True, log_block_size, rock_ridge, None, None)

    def new_dotdot(self, root, seqnum, rock_ridge, log_block_size):
        '''
        Create a new "dotdot" Directory Record.

        Parameters:
         root - The parent of this directory record.
         seqnum - The sequence number for this directory record.
         rock_ridge - Whether to make this a Rock Ridge directory record.
         log_block_size - The logical block size to use.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("Directory Record already initialized")

        self._new('\x01', root, seqnum, True, log_block_size, rock_ridge, None, None)

    def new_dir(self, name, parent, seqnum, rock_ridge, rr_name, log_block_size):
        '''
        Create a new directory Directory Record.

        Parameters:
         name - The name for this directory record.
         parent - The parent of this directory record.
         seqnum - The sequence number for this directory record.
         rock_ridge - Whether to make this a Rock Ridge directory record.
         rr_name - The Rock Ridge name for this directory record.
         log_block_size - The logical block size to use.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("Directory Record already initialized")

        self._new(name, parent, seqnum, True, log_block_size, rock_ridge, rr_name, None)

    def add_child(self, child, vd, parsing):
        '''
        A method to add a child to this object.  Note that this is called both
        during parsing and when adding a new object to the system, so it
        it shouldn't have any functionality that is not appropriate for both.

        Parameters:
         child - The child directory record object to add.
         vd - The volume descriptor to update when adding this child.
         parsing - Whether we are parsing or not; certain functionality in here
                   only works while not parsing.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("Directory Record not yet initialized")

        if not self.isdir:
            raise Exception("Trying to add a child to a record that is not a directory")

        # First ensure that this is not a duplicate.
        for c in self.children:
            if c.file_ident == child.file_ident:
                if not c.is_associated_file() and not child.is_associated_file():
                    raise PyIsoException("Parent %s already has a child named %s" % (self.file_ident, child.file_ident))

        # We keep the list of children in sorted order, based on the __lt__
        # method of this object.
        bisect.insort_left(self.children, child)

        # Check if child.dr_len will go over a boundary; if so, increase our
        # data length.
        self.curr_length += child.directory_record_length()
        if self.curr_length > self.data_length:
            if parsing:
                raise PyIsoException("More records than fit into parent directory record; ISO is corrupt")
            # When we overflow our data length, we always add a full block.
            self.data_length += vd.logical_block_size()
            # This also increases the size of the complete volume, so update
            # that here.
            vd.add_to_space_size(vd.logical_block_size())

    def remove_child(self, child, index, pvd):
        '''
        A method to remove a child from this Directory Record.

        Parameters:
         child - The child DirectoryRecord object to remove.
         index - The index of the child into this DirectoryRecord children list.
         pvd - The volume descriptor to update after removing the child.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("Directory Record not yet initialized")

        self.curr_length -= child.directory_record_length()
        if (self.data_length - self.curr_length) > pvd.logical_block_size():
            self.data_length -= pvd.logical_block_size()
            pvd.remove_from_space_size(pvd.logical_block_size())

        if child.isdir and child.rock_ridge is not None:
            if self.parent is None:
                self.children[0].rock_ridge.remove_from_file_links()
                self.children[1].rock_ridge.remove_from_file_links()
            else:
                self.rock_ridge.remove_from_file_links()
                self.children[0].rock_ridge.remove_from_file_links()

        del self.children[index]

    def is_dir(self):
        '''
        A method to determine whether this Directory Record is a directory.

        Parameters:
         None.
        Returns:
         True if this DirectoryRecord object is a directory, False otherwise.
        '''
        if not self.initialized:
            raise PyIsoException("Directory Record not yet initialized")
        return self.isdir

    def is_file(self):
        '''
        A method to determine whether this Directory Record is a file.

        Parameters:
         None.
        Returns:
         True if this DirectoryRecord object is a file, False otherwise.
        '''
        if not self.initialized:
            raise PyIsoException("Directory Record not yet initialized")
        return not self.isdir

    def is_dot(self):
        '''
        A method to determine whether this Directory Record is a "dot" entry.

        Parameters:
         None.
        Returns:
         True if this DirectoryRecord object is a "dot" entry, False otherwise.
        '''
        if not self.initialized:
            raise PyIsoException("Directory Record not yet initialized")
        return self.file_ident == '\x00'

    def is_dotdot(self):
        '''
        A method to determine whether this Directory Record is a "dotdot" entry.

        Parameters:
         None.
        Returns:
         True if this DirectoryRecord object is a "dotdot" entry, False otherwise.
        '''
        if not self.initialized:
            raise PyIsoException("Directory Record not yet initialized")
        return self.file_ident == '\x01'

    def directory_record_length(self):
        '''
        A method to determine the length of this Directory Record.

        Parameters:
         None.
        Returns:
         The length of this Directory Record.
        '''
        if not self.initialized:
            raise PyIsoException("Directory Record not yet initialized")
        return self.dr_len

    def _extent_location(self):
        '''
        An internal method to get the location of this Directory Record on the
        ISO.

        Parameters:
         None.
        Returns:
         Extent location of this Directory Record on the ISO.
        '''
        if self.new_extent_loc is None:
            return self.orig_extent_loc
        return self.new_extent_loc

    def extent_location(self):
        '''
        A method to get the location of this Directory Record on the ISO.

        Parameters:
         None.
        Returns:
         Extent location of this Directory Record on the ISO.
        '''
        if not self.initialized:
            raise PyIsoException("Directory Record not yet initialized")
        return self._extent_location()

    def file_identifier(self):
        '''
        A method to get the identifier of this Directory Record.

        Parameters:
         None.
        Returns:
         String representing the identifier of this Directory Record.
        '''
        if not self.initialized:
            raise PyIsoException("Directory Record not yet initialized")
        if self.is_root:
            return '/'
        if self.file_ident == '\x00':
            return '.'
        if self.file_ident == '\x01':
            return '..'
        return self.file_ident

    def file_length(self):
        '''
        A method to get the file length of this Directory Record.

        Parameters:
         None.
        Returns:
         Integer file length of this Directory Record.
        '''
        if not self.initialized:
            raise PyIsoException("Directory Record not yet initialized")
        return self.data_length

    def record(self):
        '''
        A method to generate the string representing this Directory Record.

        Parameters:
         None.
        Returns:
         String representing this Directory Record.
        '''
        if not self.initialized:
            raise PyIsoException("Directory Record not yet initialized")

        # Ecma-119 9.1.5 says the date should reflect the time when the
        # record was written, so we make a new date now and use that to
        # write out the record.
        self.date = DirectoryRecordDate()
        self.date.new()

        padstr = '\x00' * ((struct.calcsize(self.fmt) + self.len_fi) % 2)

        extent_loc = self._extent_location()

        ret = struct.pack(self.fmt, self.dr_len, self.xattr_len,
                          extent_loc, swab_32bit(extent_loc),
                          self.data_length, swab_32bit(self.data_length),
                          self.date.record(), self.file_flags,
                          self.file_unit_size, self.interleave_gap_size,
                          self.seqnum, swab_16bit(self.seqnum),
                          self.len_fi) + self.file_ident + padstr

        if self.rock_ridge is not None:
            ret += self.rock_ridge.record()

        ret += '\x00' * (len(ret) % 2)

        return ret

    def open_data(self, logical_block_size):
        '''
        A method to prepare the data file object for reading.  This is called
        when a higher layer wants to read data associated with this Directory
        Record, which implies that this directory record is a file.  The
        preparation consists of seeking to the appropriate location of the
        file object, based on whether this data is coming from the original
        ISO or was added later.

        Parameters:
         logical_block_size - The logical block size to use when seeking.
        Returns:
         A tuple containing a reference to the file object and the total length
         of the data for this Directory Record.
        '''
        if not self.initialized:
            raise PyIsoException("Directory Record not yet initialized")

        if self.isdir:
            raise PyIsoException("Cannot write out a directory")

        if self.original_data_location == self.DATA_ON_ORIGINAL_ISO:
            self.data_fp.seek(self.orig_extent_loc * logical_block_size)
        else:
            self.data_fp.seek(0)

        return self.data_fp,self.data_length

    def update_location(self, extent):
        '''
        Set the extent location of this Directory Record on the ISO.

        Parameters:
         extent - The new extent to set for this Directory Record.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("Directory Record not yet initialized")

        self.new_extent_loc = extent

    def is_associated_file(self):
        '''
        A method to determine whether this file is "associated" with another file
        on the ISO.

        Parameters:
         None.
        Returns:
         True if this file is associated with another file on the ISO, False
         otherwise.
        '''
        if not self.initialized:
            raise PyIsoException("Directory Record not yet initialized")

        return self.file_flags & (1 << self.FILE_FLAG_ASSOCIATED_FILE_BIT)

    def __lt__(self, other):
        # This method is used for the bisect.insort_left() when adding a child.
        # It needs to return whether self is less than other.  Here we use the
        # ISO9660 sorting order which is essentially:
        #
        # 1.  The \x00 is always the "dot" record, and is always first.
        # 2.  The \x01 is always the "dotdot" record, and is always second.
        # 3.  Other entries are sorted lexically; this does not exactly match
        #     the sorting method specified in Ecma-119, but does OK for now.
        #
        # FIXME: we need to implement Ecma-119 section 9.3 for the sorting
        # order; this essentially means padding out the shorter of the two with
        # 0x20 (spaces), then comparing byte-by-byte until they differ.
        if self.file_ident == '\x00':
            if other.file_ident == '\x00':
                return False
            return True
        if other.file_ident == '\x00':
            return False

        if self.file_ident == '\x01':
            if other.file_ident == '\x00':
                return False
            return True

        if other.file_ident == '\x01':
            # If self.file_ident was '\x00', it would have been caught above.
            return False
        return self.file_ident < other.file_ident

class PrimaryVolumeDescriptor(HeaderVolumeDescriptor):
    '''
    A class representing the Primary Volume Descriptor of this ISO.  Note that
    there can be one, and only one, Primary Volume Descriptor per ISO.  This is
    the first thing on the ISO that is parsed, and contains all of the basic
    information about the ISO.
    '''
    def __init__(self):
        HeaderVolumeDescriptor.__init__(self)
        self.fmt = "=B5sBB32s32sQLL32sHHHHHHLLLLLL34s128s128s128s128s37s37s37s17s17s17s17sBB512s653s"

    def parse(self, vd, data_fp, extent_loc):
        '''
        Parse a primary volume descriptor out of a string.

        Parameters:
         vd - The string containing the Primary Volume Descriptor.
         data_fp - A file object containing the root directory record.
         extent_loc - Ignored, extent location is fixed for the Primary Volume
                      Descriptor.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("This Primary Volume Descriptor is already initialized")

        # According to Ecma-119, we have to parse both the
        # little-endian and bit-endian versions of:
        #
        # Space Size
        # Set Size
        # Seq Num
        # Logical Block Size
        # Path Table Size
        # Path Table Location
        # Optional Path Table Location
        #
        # In doing this, we:
        # a) Check to make sure that the little-endian and big-endian
        # versions agree with each other.
        # b) Only store one type in the class, and generate the other one
        # as necessary.
        (self.descriptor_type, self.identifier, self.version, unused1,
         self.system_identifier, self.volume_identifier, unused2,
         space_size_le, space_size_be, unused3, set_size_le, set_size_be,
         seqnum_le, seqnum_be, logical_block_size_le, logical_block_size_be,
         path_table_size_le, path_table_size_be, self.path_table_location_le,
         self.optional_path_table_location_le, self.path_table_location_be,
         self.optional_path_table_location_be, root_dir_record,
         self.volume_set_identifier, pub_ident_str, prepare_ident_str,
         app_ident_str, self.copyright_file_identifier,
         self.abstract_file_identifier, self.bibliographic_file_identifier,
         vol_create_date_str, vol_mod_date_str, vol_expire_date_str,
         vol_effective_date_str, self.file_structure_version, unused4,
         self.application_use, unused5) = struct.unpack(self.fmt, vd)

        # According to Ecma-119, 8.4.1, the primary volume descriptor type
        # should be 1.
        if self.descriptor_type != VOLUME_DESCRIPTOR_TYPE_PRIMARY:
            raise PyIsoException("Invalid primary volume descriptor")
        # According to Ecma-119, 8.4.2, the identifier should be "CD001".
        if self.identifier != "CD001":
            raise PyIsoException("invalid CD isoIdentification")
        # According to Ecma-119, 8.4.3, the version should be 1.
        if self.version != 1:
            raise PyIsoException("Invalid primary volume descriptor version")
        # According to Ecma-119, 8.4.4, the first unused field should be 0.
        if unused1 != 0:
            raise PyIsoException("data in unused field not zero")
        # According to Ecma-119, 8.4.5, the second unused field (after the
        # system identifier and volume identifier) should be 0.
        if unused2 != 0:
            raise PyIsoException("data in 2nd unused field not zero")
        # According to Ecma-119, 8.4.9, the third unused field should be all 0.
        if unused3 != '\x00'*32:
            raise PyIsoException("data in 3rd unused field not zero")
        # According to Ecma-119, 8.4.30, the file structure version should be 1.
        if self.file_structure_version != 1:
            raise PyIsoException("File structure version expected to be 1")
        # According to Ecma-119, 8.4.31, the fourth unused field should be 0.
        if unused4 != 0:
            raise PyIsoException("data in 4th unused field not zero")
        # According to Ecma-119, the last 653 bytes of the PVD should be all 0.
        if unused5 != '\x00'*653:
            raise PyIsoException("data in 5th unused field not zero")

        # Check to make sure that the little-endian and big-endian versions
        # of the parsed data agree with each other.
        if space_size_le != swab_32bit(space_size_be):
            raise PyIsoException("Little-endian and big-endian space size disagree")
        self.space_size = space_size_le

        if set_size_le != swab_16bit(set_size_be):
            raise PyIsoException("Little-endian and big-endian set size disagree")
        self.set_size = set_size_le

        if seqnum_le != swab_16bit(seqnum_be):
            raise PyIsoException("Little-endian and big-endian seqnum disagree")
        self.seqnum = seqnum_le

        if logical_block_size_le != swab_16bit(logical_block_size_be):
            raise PyIsoException("Little-endian and big-endian logical block size disagree")
        self.log_block_size = logical_block_size_le

        if path_table_size_le != swab_32bit(path_table_size_be):
            raise PyIsoException("Little-endian and big-endian path table size disagree")
        self.path_tbl_size = path_table_size_le
        self.path_table_num_extents = ceiling_div(self.path_tbl_size, 4096) * 2

        self.path_table_location_be = swab_32bit(self.path_table_location_be)

        self.publisher_identifier = FileOrTextIdentifier()
        self.publisher_identifier.parse(pub_ident_str, True)
        self.preparer_identifier = FileOrTextIdentifier()
        self.preparer_identifier.parse(prepare_ident_str, True)
        self.application_identifier = FileOrTextIdentifier()
        self.application_identifier.parse(app_ident_str, True)
        self.volume_creation_date = VolumeDescriptorDate()
        self.volume_creation_date.parse(vol_create_date_str)
        self.volume_modification_date = VolumeDescriptorDate()
        self.volume_modification_date.parse(vol_mod_date_str)
        self.volume_expiration_date = VolumeDescriptorDate()
        self.volume_expiration_date.parse(vol_expire_date_str)
        self.volume_effective_date = VolumeDescriptorDate()
        self.volume_effective_date.parse(vol_effective_date_str)
        self.root_dir_record = DirectoryRecord()
        self.root_dir_record.parse(root_dir_record, data_fp, None, self.log_block_size)

        self.initialized = True

    def new(self, flags, sys_ident, vol_ident, set_size, seqnum, log_block_size,
            vol_set_ident, pub_ident, preparer_ident, app_ident,
            copyright_file, abstract_file, bibli_file, vol_expire_date,
            app_use):
        '''
        Create a new Primary Volume Descriptor.

        Parameters:
         flags - Ignored.
         sys_ident - The system identification string to use on the new ISO.
         vol_ident - The volume identification string to use on the new ISO.
         set_size - The size of the set of ISOs this ISO is a part of.
         seqnum - The sequence number of the set of this ISO.
         log_block_size - The logical block size to use for the ISO.  While
                          ISO9660 technically supports sizes other than 2048
                          (the default), this almost certainly doesn't work.
         vol_set_ident - The volume set identification string to use on the
                         new ISO.
         pub_ident - The publisher identification string to use on the new ISO.
         preparer_ident - The preparer identification string to use on the new
                          ISO.
         app_ident - The application identification string to use on the new
                     ISO.
         copyright_file - The name of a file at the root of the ISO to use as
                          the copyright file.
         abstract_file - The name of a file at the root of the ISO to use as the
                         abstract file.
         bibli_file - The name of a file at the root of the ISO to use as the
                      bibliographic file.
         vol_expire_date - The date that this ISO will expire at.
         app_use - Arbitrary data that the application can stuff into the
                   primary volume descriptor of this ISO.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("This Primary Volume Descriptor is already initialized")

        if flags != 0:
            raise PyIsoException("Non-zero flags not allowed for a PVD")

        self.descriptor_type = VOLUME_DESCRIPTOR_TYPE_PRIMARY
        self.identifier = "CD001"
        self.version = 1

        if len(sys_ident) > 32:
            raise PyIsoException("The system identifer has a maximum length of 32")
        self.system_identifier = "{:<32}".format(sys_ident)

        if len(vol_ident) > 32:
            raise PyIsoException("The volume identifier has a maximum length of 32")
        self.volume_identifier = "{:<32}".format(vol_ident)

        # The space_size is the number of extents (2048-byte blocks) in the
        # ISO.  We know we will at least have the system area (16 extents),
        # the PVD (1 extent), the Volume Terminator (2 extents), 2 extents
        # for the little endian path table record, 2 extents for the big endian
        # path table record, and 1 extent for the root directory record,
        # for a total of 24 extents to start with.
        self.space_size = 24
        self.set_size = set_size
        if seqnum > set_size:
            raise PyIsoException("Sequence number must be less than or equal to set size")
        self.seqnum = seqnum
        self.log_block_size = log_block_size
        # The path table size is in bytes, and is always at least 10 bytes
        # (for the root directory record).
        self.path_tbl_size = 10
        self.path_table_num_extents = ceiling_div(self.path_tbl_size, 4096) * 2
        # By default the Little Endian Path Table record starts at extent 19
        # (right after the Volume Terminator).
        self.path_table_location_le = 19
        # By default the Big Endian Path Table record starts at extent 21
        # (two extents after the Little Endian Path Table Record).
        self.path_table_location_be = 21
        # FIXME: we don't support the optional path table location right now
        self.optional_path_table_location_le = 0
        self.optional_path_table_location_be = 0
        self.root_dir_record = DirectoryRecord()
        self.root_dir_record.new_root(seqnum, self.log_block_size)

        if len(vol_set_ident) > 128:
            raise PyIsoException("The maximum length for the volume set identifier is 128")
        self.volume_set_identifier = "{:<128}".format(vol_set_ident)

        self.publisher_identifier = pub_ident
        self.publisher_identifier.check_filename(True)

        self.preparer_identifier = preparer_ident
        self.preparer_identifier.check_filename(True)

        self.application_identifier = app_ident
        self.application_identifier.check_filename(True)

        self.copyright_file_identifier = "{:<37}".format(copyright_file)
        self.abstract_file_identifier = "{:<37}".format(abstract_file)
        self.bibliographic_file_identifier = "{:<37}".format(bibli_file)

        # We make a valid volume creation and volume modification date here,
        # but they will get overwritten during writeout.
        now = time.time()
        self.volume_creation_date = VolumeDescriptorDate()
        self.volume_creation_date.new(now)
        self.volume_modification_date = VolumeDescriptorDate()
        self.volume_modification_date.new(now)
        self.volume_expiration_date = VolumeDescriptorDate()
        self.volume_expiration_date.new(vol_expire_date)
        self.volume_effective_date = VolumeDescriptorDate()
        self.volume_effective_date.new(now)
        self.file_structure_version = 1

        if len(app_use) > 512:
            raise PyIsoException("The maximum length for the application use is 512")
        self.application_use = "{:<512}".format(app_use)

        self.initialized = True

    def record(self):
        '''
        A method to generate the string representing this Primary Volume
        Descriptor.

        Parameters:
         None.
        Returns:
         A string representing this Primary Volume Descriptor.
        '''
        if not self.initialized:
            raise PyIsoException("This Primary Volume Descriptor is not yet initialized")

        now = time.time()

        vol_create_date = VolumeDescriptorDate()
        vol_create_date.new(now)

        vol_mod_date = VolumeDescriptorDate()
        vol_mod_date.new(now)

        return struct.pack(self.fmt, self.descriptor_type, self.identifier,
                           self.version, 0, self.system_identifier,
                           self.volume_identifier, 0, self.space_size,
                           swab_32bit(self.space_size), '\x00'*32,
                           self.set_size, swab_16bit(self.set_size),
                           self.seqnum, swab_16bit(self.seqnum),
                           self.log_block_size, swab_16bit(self.log_block_size),
                           self.path_tbl_size, swab_32bit(self.path_tbl_size),
                           self.path_table_location_le,
                           self.optional_path_table_location_le,
                           swab_32bit(self.path_table_location_be),
                           self.optional_path_table_location_be,
                           self.root_dir_record.record(),
                           self.volume_set_identifier,
                           self.publisher_identifier.record(),
                           self.preparer_identifier.record(),
                           self.application_identifier.record(),
                           self.copyright_file_identifier,
                           self.abstract_file_identifier,
                           self.bibliographic_file_identifier,
                           vol_create_date.record(),
                           vol_mod_date.record(),
                           self.volume_expiration_date.record(),
                           self.volume_effective_date.record(),
                           self.file_structure_version, 0, self.application_use,
                           "\x00" * 653)

    @staticmethod
    def extent_location():
        '''
        A class method to return the Primary Volume Descriptors extent location.
        '''
        return 16

class VolumeDescriptorSetTerminator(object):
    '''
    A class that represents a Volume Descriptor Set Terminator.  The VDST
    signals the end of volume descriptors on the ISO.
    '''
    def __init__(self):
        self.initialized = False
        self.fmt = "=B5sB2041s"

    def parse(self, vd, extent):
        '''
        A method to parse a Volume Descriptor Set Terminator out of a string.

        Parameters:
         vd - The string to parse.
         extent - The extent this VDST is currently located at.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("Volume Descriptor Set Terminator already initialized")

        (self.descriptor_type, self.identifier, self.version,
         unused) = struct.unpack(self.fmt, vd)

        # According to Ecma-119, 8.3.1, the volume descriptor set terminator
        # type should be 255
        if self.descriptor_type != VOLUME_DESCRIPTOR_TYPE_SET_TERMINATOR:
            raise PyIsoException("Invalid descriptor type")
        # According to Ecma-119, 8.3.2, the identifier should be "CD001"
        if self.identifier != 'CD001':
            raise PyIsoException("Invalid identifier")
        # According to Ecma-119, 8.3.3, the version should be 1
        if self.version != 1:
            raise PyIsoException("Invalid version")
        # According to Ecma-119, 8.3.4, the rest of the terminator should be 0;
        # however, we have seen ISOs in the wild that put stuff into this field.
        # Just ignore it.

        self.orig_extent_loc = extent
        self.new_extent_loc = None

        self.initialized = True

    def new(self):
        '''
        A method to create a new Volume Descriptor Set Terminator.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("Volume Descriptor Set Terminator already initialized")

        self.descriptor_type = VOLUME_DESCRIPTOR_TYPE_SET_TERMINATOR
        self.identifier = "CD001"
        self.version = 1
        self.orig_extent_loc = None
        # This will get set during reshuffle_extent.
        self.new_extent_loc = 0

        self.initialized = True

    def record(self):
        '''
        A method to generate a string representing this Volume Descriptor Set
        Terminator.

        Parameters:
         None.
        Returns:
         String representing this Volume Descriptor Set Terminator.
        '''
        if not self.initialized:
            raise PyIsoException("Volume Descriptor Set Terminator not yet initialized")
        return struct.pack(self.fmt, self.descriptor_type,
                           self.identifier, self.version, "\x00" * 2041)

    def extent_location(self):
        '''
        A method to get this Volume Descriptor Set Terminator's extent location.

        Parameters:
         None.
        Returns:
         Integer extent location.
        '''
        if not self.initialized:
            raise PyIsoException("Volume Descriptor Set Terminator not yet initialized")

        if self.new_extent_loc is None:
            return self.orig_extent_loc
        return self.new_extent_loc

class EltoritoValidationEntry(object):
    '''
    A class that represents an El Torito Validation Entry.  El Torito requires
    that the first entry in the El Torito Boot Catalog be a validation entry.
    '''
    def __init__(self):
        self.initialized = False
        # An El Torito validation entry consists of:
        # Offset 0x0:       Header ID (0x1)
        # Offset 0x1:       Platform ID (0 for x86, 1 for PPC, 2 for Mac)
        # Offset 0x2-0x3:   Reserved, must be 0
        # Offset 0x4-0x1b:  ID String for manufacturer of CD
        # Offset 0x1c-0x1d: Checksum of all bytes.
        # Offset 0x1e:      Key byte 0x55
        # Offset 0x1f:      Key byte 0xaa
        self.fmt = "=BBH24sHBB"

    @staticmethod
    def _checksum(data):
        '''
        A static method to compute the checksum on the ISO.  Note that this is
        *not* a 1's complement checksum; when an addition overflows, the carry
        bit is discarded, not added to the end.
        '''
        s = 0
        for i in range(0, len(data), 2):
            w = ord(data[i]) + (ord(data[i+1]) << 8)
            s = (s + w) & 0xffff
        return s

    def parse(self, valstr):
        '''
        A method to parse an El Torito Validation Entry out of a string.

        Parameters:
         valstr - The string to parse the El Torito Validation Entry out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("El Torito Validation Entry already initialized")

        (self.header_id, self.platform_id, reserved, self.id_string,
         self.checksum, self.keybyte1,
         self.keybyte2) = struct.unpack(self.fmt, valstr)

        if self.header_id != 1:
            raise PyIsoException("El Torito Validation entry header ID not 1")

        if self.platform_id not in [0, 1, 2]:
            raise PyIsoException("El Torito Validation entry platform ID not valid")

        if self.keybyte1 != 0x55:
            raise PyIsoException("El Torito Validation entry first keybyte not 0x55")
        if self.keybyte2 != 0xaa:
            raise PyIsoException("El Torito Validation entry second keybyte not 0xaa")

        # Now that we've done basic checking, calculate the checksum of the
        # validation entry and make sure it is right.
        if self._checksum(valstr) != 0:
            raise PyIsoException("El Torito Validation entry checksum not correct")

        self.initialized = True

    def new(self):
        '''
        A method to create a new El Torito Validation Entry.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("El Torito Validation Entry already initialized")

        self.header_id = 1
        self.platform_id = 0 # FIXME: let the user set this
        self.id_string = "\x00"*24 # FIXME: let the user set this
        self.keybyte1 = 0x55
        self.keybyte2 = 0xaa
        self.checksum = 0
        self.checksum = swab_16bit(self._checksum(self._record()) - 1)
        self.initialized = True

    def _record(self):
        '''
        An internal method to generate a string representing this El Torito
        Validation Entry.

        Parameters:
         None.
        Returns:
         String representing this El Torito Validation Entry.
        '''
        return struct.pack(self.fmt, self.header_id, self.platform_id, 0, self.id_string, self.checksum, self.keybyte1, self.keybyte2)

    def record(self):
        '''
        A method to generate a string representing this El Torito Validation
        Entry.

        Parameters:
         None.
        Returns:
         String representing this El Torito Validation Entry.
        '''
        if not self.initialized:
            raise PyIsoException("El Torito Validation Entry not yet initialized")

        return self._record()

class EltoritoInitialEntry(object):
    '''
    A class that represents an El Torito Initial Entry.  El Torito requires that
    there is one initial entry in an El Torito Boot Catalog.
    '''
    def __init__(self):
        self.initialized = False
        # An El Torito initial entry consists of:
        # Offset 0x0:      Boot indicator (0x88 for bootable, 0x00 for
        #                  non-bootable)
        # Offset 0x1:      Boot media type.  One of 0x0 for no emulation,
        #                  0x1 for 1.2M diskette emulation, 0x2 for 1.44M
        #                  diskette emulation, 0x3 for 2.88M diskette
        #                  emulation, or 0x4 for Hard Disk emulation.
        # Offset 0x2-0x3:  Load Segment - if 0, use traditional 0x7C0.
        # Offset 0x4:      System Type - copy of Partition Table byte 5
        # Offset 0x5:      Unused, must be 0
        # Offset 0x6-0x7:  Sector Count - Number of virtual sectors to store
        #                  during initial boot.
        # Offset 0x8-0xb:  Load RBA - Start address of virtual disk.
        # Offset 0xc-0x1f: Unused, must be 0.
        self.fmt = "=BBHBBHL20s"

    def parse(self, valstr):
        '''
        A method to parse an El Torito Initial Entry out of a string.

        Parameters:
         valstr - The string to parse the El Torito Initial Entry out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("El Torito Initial Entry already initialized")

        (self.boot_indicator, self.boot_media_type, self.load_segment,
         self.system_type, unused1, self.sector_count, self.load_rba,
         unused2) = struct.unpack(self.fmt, valstr)

        if self.boot_indicator not in [0x88, 0x00]:
            raise PyIsoException("Invalid eltorito initial entry boot indicator")
        if self.boot_media_type > 4:
            raise PyIsoException("Invalid eltorito boot media type")

        # FIXME: check that the system type matches the partition table

        if unused1 != 0:
            raise PyIsoException("El Torito unused field must be 0")

        # According to the specification, the El Torito unused end field (bytes
        # 0xc - 0x1f, unused2 field) should be all zero.  However, we have found
        # ISOs in the wild where that is not the case, so skip that particular
        # check here.

        self.initialized = True

    def new(self, sector_count):
        '''
        A method to create a new El Torito Initial Entry.

        Parameters:
         sector_count - The number of sectors to assign to this El Torito
                        Initial Entry.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("El Torito Initial Entry already initialized")

        self.boot_indicator = 0x88 # FIXME: let the user set this
        self.boot_media_type = 0 # FIXME: let the user set this
        self.load_segment = 0x0 # FIXME: let the user set this
        self.system_type = 0
        self.sector_count = sector_count
        self.load_rba = 0 # This will get set later

        self.initialized = True

    def set_rba(self, new_rba):
        '''
        A method to set the load_rba for this El Torito Initial Entry.

        Parameters:
         new_rba - The new address to set for the El Torito Initial Entry.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("El Torito Initial Entry not yet initialized")

        self.load_rba = new_rba

    def record(self):
        '''
        A method to generate a string representing this El Torito Initial
        Entry.

        Parameters:
         None.
        Returns:
         String representing this El Torito Initial Entry.
        '''
        if not self.initialized:
            raise PyIsoException("El Torito Initial Entry not yet initialized")

        return struct.pack(self.fmt, self.boot_indicator, self.boot_media_type,
                           self.load_segment, self.system_type, 0,
                           self.sector_count, self.load_rba, '\x00'*20)

class EltoritoSectionHeader(object):
    '''
    A class that represents an El Torito Section Header.
    '''
    def __init__(self):
        self.initialized = False
        self.fmt = "=BBH28s"

    def parse(self, valstr):
        '''
        Parse an El Torito section header from a string.

        Parameters:
         valstr - The string to parse.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("El Torito Section Header already initialized")

        (self.header_indicator, self.platform_id, self.num_section_entries,
         self.id_string) = struct.unpack(self.fmt, valstr)

        self.initialized = True

    def new(self, id_string):
        '''
        Create a new El Torito section header.

        Parameters:
         id_string - The ID to use for this section header.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("El Torito Section Header already initialized")

        self.header_indicator = 0x90 # FIXME: how should we deal with this?
        self.platform_id = 0 # FIXME: we should allow the user to set this
        self.num_section_entries = 0
        self.id_string = id_string
        self.initialized = True

    def record(self):
        '''
        Get a string representing this El Torito section header.

        Parameters:
         None.
        Returns:
         A string representing this El Torito section header.
        '''
        if not self.initialized:
            raise PyIsoException("El Torito Section Header not yet initialized")

        return struct.pack(self.fmt, self.header_indicator, self.platform_id,
                           self.num_section_entries, self.id_string)

class EltoritoSectionEntry(object):
    '''
    A class that represents an El Torito Section Entry.
    '''
    def __init__(self):
        self.initialized = False
        self.fmt = "=BBHBBHLB19s"

    def parse(self, valstr):
        '''
        Parse an El Torito section entry from a string.

        Parameters:
         valstr - The string to parse.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("El Torito Section Header already initialized")

        (self.boot_indicator, self.boot_media_type, self.load_segment,
         self.system_type, unused1, self.sector_count, self.load_rba,
         self.selection_criteria_type,
         self.selection_criteria) = struct.unpack(self.fmt, valstr)

        # FIXME: check that the system type matches the partition table

        if unused1 != 0:
            raise PyIsoException("El Torito unused field must be 0")

        self.initialized = True

    def new(self):
        '''
        Create a new El Torito section header.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("El Torito Section Header already initialized")

        self.boot_indicator = 0x88 # FIXME: allow the user to set this
        self.boot_media_type = 0x0 # FIXME: allow the user to set this
        self.load_segment = 0 # FIXME: allow the user to set this
        self.system_type = 0 # FIXME: we should copy this from the partition table
        self.sector_count = 0 # FIXME: allow the user to set this
        self.load_rba = 0 # FIXME: set this as appropriate
        self.selection_criteria_type = 0 # FIXME: allow the user to set this
        self.selection_criteria = "{:\x00<19}".format('') # FIXME: allow user to set this
        self.initialized = True

    def record(self):
        '''
        Get a string representing this El Torito section header.

        Parameters:
         None.
        Returns:
         A string representing this El Torito section header.
        '''
        return struct.pack(self.fmt, self.boot_indicator, self.boot_media_type,
                           self.load_segment, self.system_type, 0,
                           self.sector_count, self.load_rba,
                           self.selection_criteria_type,
                           self.selection_criteria)

class EltoritoBootCatalog(object):
    '''
    A class that represents an El Torito Boot Catalog.  The boot catalog is the
    basic unit of El Torito, and is expected to contain a validation entry,
    an initial entry, and zero or more section entries.
    '''
    EXPECTING_VALIDATION_ENTRY = 1
    EXPECTING_INITIAL_ENTRY = 2
    EXPECTING_SECTION_HEADER_OR_DONE = 3
    EXPECTING_SECTION_ENTRY = 4

    def __init__(self, br):
        self.dirrecord = None
        self.initialized = False
        self.br = br
        self.initial_entry = None
        self.validation_entry = None
        self.section_entries = []
        self.state = self.EXPECTING_VALIDATION_ENTRY

    def parse(self, valstr):
        '''
        A method to parse an El Torito Boot Catalog out of a string.

        Parameters:
         valstr - The string to parse the El Torito Boot Catalog out of.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("El Torito Boot Catalog already initialized")

        if self.state == self.EXPECTING_VALIDATION_ENTRY:
            # The first entry in an El Torito boot catalog is the Validation
            # Entry.  A Validation entry consists of 32 bytes (described in
            # detail in the parse_eltorito_validation_entry() method).
            self.validation_entry = EltoritoValidationEntry()
            self.validation_entry.parse(valstr)
            self.state = self.EXPECTING_INITIAL_ENTRY
        elif self.state == self.EXPECTING_INITIAL_ENTRY:
            # The next entry is the Initial/Default entry.  An Initial/Default
            # entry consists of 32 bytes (described in detail in the
            # parse_eltorito_initial_entry() method).
            self.initial_entry = EltoritoInitialEntry()
            self.initial_entry.parse(valstr)
            self.state = self.EXPECTING_SECTION_HEADER_OR_DONE
        else:
            if valstr[0] == '\x00':
                # An empty entry tells us we are done parsing El Torito, so make
                # sure we got what we expected and then set ourselves as
                # initialized.
                self.initialized = True
            elif valstr[0] == '\x90' or valstr[0] == '\x91':
                # A Section Header Entry
                self.section_header = EltoritoSectionHeader()
                self.section_header.parse(valstr)
                if valstr[0] == '\x91':
                    self.state = self.EXPECTING_SECTION_ENTRY
            elif valstr[0] == '\x88' or valstr[0] == '\x00':
                # A Section Entry
                secentry = EltoritoSectionEntry()
                secentry.parse(valstr)
                self.section_entries.append(secentry)
            elif valstr[0] == '\x44':
                # A Section Entry Extension
                self.section_entries[-1].selection_criteria += valstr[2:]
            else:
                raise PyIsoException("Invalid El Torito Boot Catalog entry")

        return self.initialized

    def new(self, br, sector_count):
        '''
        A method to create a new El Torito Boot Catalog.

        Parameters:
         br - The boot record that this El Torito Boot Catalog is associated
              with.
         sector_count - The number of sectors for the initial entry.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise Exception("El Torito Boot Catalog already initialized")

        # Create the El Torito validation entry
        self.validation_entry = EltoritoValidationEntry()
        self.validation_entry.new()

        self.initial_entry = EltoritoInitialEntry()
        self.initial_entry.new(sector_count)

        self.br = br

        self.initialized = True

    def record(self):
        '''
        A method to generate a string representing this El Torito Boot Catalog.

        Parameters:
         None.
        Returns:
         A string representing this El Torito Boot Catalog.
        '''
        if not self.initialized:
            raise PyIsoException("El Torito Boot Catalog not yet initialized")

        return self.validation_entry.record() + self.initial_entry.record()

    def update_initial_entry_location(self, new_rba):
        '''
        A method to update the initial entry location.

        Parameters:
         new_rba - The new extent location to associate with the initial entry.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("El Torito Boot Catalog not yet initialized")

        self.initial_entry.set_rba(new_rba)

    def set_dirrecord(self, rec):
        '''
        A method to update the directory record associated with this El Torito
        Boot Catalog.  While not explicitly mentioned in the standard, all
        known implemenations of El Torito associate a "fake" file with the
        El Torito Boot Catalog; this call connects the fake directory record
        with this boot catalog.

        Parameters:
         rec - The DirectoryRecord object assocatied with this Boot Catalog
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("El Torito Boot Catalog not yet initialized")

        self.dirrecord = rec

    def set_initial_entry_dirrecord(self, rec):
        '''
        A method to update the directory record associated with the initial
        entry of this boot catalog.

        Parameters:
         rec - The DirectoryRecord object associated with the initial entry.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("El Torito Boot Catalog not yet initialized")

        self.initial_entry_dirrecord = rec

    def extent_location(self):
        '''
        A method to get the extent location of this El Torito Boot Catalog.

        Parameters:
         None.
        Returns:
         Integer extent location of this El Torito Boot Catalog.
        '''
        if not self.initialized:
            raise PyIsoException("El Torito Boot Catalog not yet initialized")

        return struct.unpack("=L", self.br.boot_system_use[:4])[0]

class BootRecord(object):
    '''
    A class representing an ISO9660 Boot Record.
    '''
    def __init__(self):
        self.initialized = False
        self.fmt = "=B5sB32s32s1977s"

    def parse(self, vd, extent_loc):
        '''
        A method to parse a Boot Record out of a string.

        Parameters:
         vd - The string to parse the Boot Record out of.
         extent_loc - The extent location this Boot Record is current at.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("Boot Record already initialized")

        (self.descriptor_type, self.identifier, self.version,
         self.boot_system_identifier, self.boot_identifier,
         self.boot_system_use) = struct.unpack(self.fmt, vd)

        # According to Ecma-119, 8.2.1, the boot record type should be 0
        if self.descriptor_type != VOLUME_DESCRIPTOR_TYPE_BOOT_RECORD:
            raise PyIsoException("Invalid descriptor type")
        # According to Ecma-119, 8.2.2, the identifier should be "CD001"
        if self.identifier != 'CD001':
            raise PyIsoException("Invalid identifier")
        # According to Ecma-119, 8.2.3, the version should be 1
        if self.version != 1:
            raise PyIsoException("Invalid version")

        self.orig_extent_loc = extent_loc
        self.new_extent_loc = None

        self.initialized = True

    def new(self, boot_system_id):
        '''
        A method to create a new Boot Record.

        Parameters:
         boot_system_id - The system identifier to associate with this Boot
                          Record.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise Exception("Boot Record already initialized")

        self.descriptor_type = VOLUME_DESCRIPTOR_TYPE_BOOT_RECORD
        self.identifier = "CD001"
        self.version = 1
        self.boot_system_identifier = "{:\x00<32}".format(boot_system_id)
        self.boot_identifier = "\x00"*32 # FIXME: we may want to allow the user to set this
        self.boot_system_use = "\x00"*197 # This will be set later

        self.orig_extent_loc = None
        # This is wrong, but will be corrected at reshuffle_extent time.
        self.new_extent_loc = 0

        self.initialized = True

    def record(self):
        '''
        A method to generate a string representing this Boot Record.

        Parameters:
         None.
        Returns:
         A string representing this Boot Record.
        '''
        if not self.initialized:
            raise PyIsoException("Boot Record not yet initialized")

        return struct.pack(self.fmt, self.descriptor_type, self.identifier,
                           self.version, self.boot_system_identifier,
                           self.boot_identifier, self.boot_system_use)

    def update_boot_system_use(self, boot_sys_use):
        '''
        A method to update the boot system use field of this Boot Record.

        Parameters:
         boot_sys_use - The new boot system use field for this Boot Record.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("Boot Record not yet initialized")

        self.boot_system_use = "{:\x00<197}".format(boot_sys_use)

    def extent_location(self):
        '''
        A method to get the extent locaion of this Boot Record.

        Parameters:
         None.
        Returns:
         Integer extent location of this Boot Record.
        '''
        if not self.initialized:
            raise PyIsoException("Boot Record not yet initialized")

        if self.new_extent_loc is None:
            return self.orig_extent_loc
        return self.new_extent_loc

class SupplementaryVolumeDescriptor(HeaderVolumeDescriptor):
    '''
    A class that represents an ISO9660 Supplementary Volume Descriptor (used
    for Joliet records, among other things).
    '''
    def __init__(self):
        HeaderVolumeDescriptor.__init__(self)
        self.fmt = "=B5sBB32s32sQLL32sHHHHHHLLLLLL34s128s128s128s128s37s37s37s17s17s17s17sBB512s653s"

    def parse(self, vd, data_fp, extent):
        '''
        A method to parse a Supplementary Volume Descriptor from a string.

        Parameters:
         vd - The string to parse the Supplementary Volume Descriptor from.
         data_fp - The file object to associate with the root directory record.
         extent - The extent location of this Supplementary Volume Descriptor.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("Supplementary Volume Descriptor already initialized")

        (self.descriptor_type, self.identifier, self.version, self.flags,
         self.system_identifier, self.volume_identifier, unused1,
         space_size_le, space_size_be, self.escape_sequences, set_size_le,
         set_size_be, seqnum_le, seqnum_be, logical_block_size_le,
         logical_block_size_be, path_table_size_le, path_table_size_be,
         self.path_table_location_le, self.optional_path_table_location_le,
         self.path_table_location_be, self.optional_path_table_location_be,
         root_dir_record, self.volume_set_identifier, pub_ident_str,
         prepare_ident_str, app_ident_str, self.copyright_file_identifier,
         self.abstract_file_identifier, self.bibliographic_file_identifier,
         vol_create_date_str, vol_mod_date_str, vol_expire_date_str,
         vol_effective_date_str, self.file_structure_version, unused2,
         self.application_use, unused3) = struct.unpack(self.fmt, vd)

        # According to Ecma-119, 8.5.1, the supplementary volume descriptor type
        # should be 2.
        if self.descriptor_type != VOLUME_DESCRIPTOR_TYPE_SUPPLEMENTARY:
            raise PyIsoException("Invalid supplementary volume descriptor")
        # According to Ecma-119, 8.4.2, the identifier should be "CD001".
        if self.identifier != "CD001":
            raise PyIsoException("invalid CD isoIdentification")
        # According to Ecma-119, 8.5.2, the version should be 1.
        if self.version != 1:
            raise PyIsoException("Invalid primary volume descriptor version")
        # According to Ecma-119, 8.4.5, the first unused field (after the
        # system identifier and volume identifier) should be 0.
        if unused1 != 0:
            raise PyIsoException("data in 2nd unused field not zero")
        if self.file_structure_version != 1:
            raise PyIsoException("File structure version expected to be 1")
        if unused2 != 0:
            raise PyIsoException("data in 4th unused field not zero")
        if unused3 != '\x00'*653:
            raise PyIsoException("data in 5th unused field not zero")

        # Check to make sure that the little-endian and big-endian versions
        # of the parsed data agree with each other
        if space_size_le != swab_32bit(space_size_be):
            raise PyIsoException("Little-endian and big-endian space size disagree")
        self.space_size = space_size_le

        if set_size_le != swab_16bit(set_size_be):
            raise PyIsoException("Little-endian and big-endian set size disagree")
        self.set_size = set_size_le

        if seqnum_le != swab_16bit(seqnum_be):
            raise PyIsoException("Little-endian and big-endian seqnum disagree")
        self.seqnum = seqnum_le

        if logical_block_size_le != swab_16bit(logical_block_size_be):
            raise PyIsoException("Little-endian and big-endian logical block size disagree")
        self.log_block_size = logical_block_size_le

        if path_table_size_le != swab_32bit(path_table_size_be):
            raise PyIsoException("Little-endian and big-endian path table size disagree")
        self.path_tbl_size = path_table_size_le
        self.path_table_num_extents = ceiling_div(self.path_tbl_size, 4096) * 2

        self.path_table_location_be = swab_32bit(self.path_table_location_be)

        self.publisher_identifier = FileOrTextIdentifier()
        self.publisher_identifier.parse(pub_ident_str, False)
        self.preparer_identifier = FileOrTextIdentifier()
        self.preparer_identifier.parse(prepare_ident_str, False)
        self.application_identifier = FileOrTextIdentifier()
        self.application_identifier.parse(app_ident_str, False)
        self.volume_creation_date = VolumeDescriptorDate()
        self.volume_creation_date.parse(vol_create_date_str)
        self.volume_modification_date = VolumeDescriptorDate()
        self.volume_modification_date.parse(vol_mod_date_str)
        self.volume_expiration_date = VolumeDescriptorDate()
        self.volume_expiration_date.parse(vol_expire_date_str)
        self.volume_effective_date = VolumeDescriptorDate()
        self.volume_effective_date.parse(vol_effective_date_str)
        self.root_dir_record = DirectoryRecord()
        self.root_dir_record.parse(root_dir_record, data_fp, None, self.log_block_size)

        self.joliet = False
        if (self.flags & 0x1) == 0 and self.escape_sequences[:3] in ['%/@', '%/C', '%/E']:
            self.joliet = True

        self.orig_extent_loc = extent
        self.new_extent_loc = None

        self.initialized = True

    def new(self, flags, sys_ident, vol_ident, set_size, seqnum, log_block_size,
            vol_set_ident, pub_ident, preparer_ident, app_ident,
            copyright_file, abstract_file, bibli_file, vol_expire_date,
            app_use):
        '''
        A method to create a new Supplementary Volume Descriptor.

        Parameters:
         flags - Optional flags to set for the header.
         sys_ident - The system identification string to use on the new ISO.
         vol_ident - The volume identification string to use on the new ISO.
         set_size - The size of the set of ISOs this ISO is a part of.
         seqnum - The sequence number of the set of this ISO.
         log_block_size - The logical block size to use for the ISO.  While
                          ISO9660 technically supports sizes other than 2048
                          (the default), this almost certainly doesn't work.
         vol_set_ident - The volume set identification string to use on the
                         new ISO.
         pub_ident_str - The publisher identification string to use on the
                         new ISO.
         preparer_ident_str - The preparer identification string to use on the
                              new ISO.
         app_ident_str - The application identification string to use on the
                         new ISO.
         copyright_file - The name of a file at the root of the ISO to use as
                          the copyright file.
         abstract_file - The name of a file at the root of the ISO to use as the
                         abstract file.
         bibli_file - The name of a file at the root of the ISO to use as the
                      bibliographic file.
         vol_expire_date - The date that this ISO will expire at.
         app_use - Arbitrary data that the application can stuff into the
                   primary volume descriptor of this ISO.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("This Supplementary Volume Descriptor is already initialized")

        self.descriptor_type = VOLUME_DESCRIPTOR_TYPE_SUPPLEMENTARY
        self.identifier = "CD001"
        self.version = 1
        self.flags = flags

        if len(sys_ident) > 32:
            raise PyIsoException("The system identifer has a maximum length of 32")
        self.system_identifier = "{:<32}".format(sys_ident.encode('utf-16_be'))

        if len(vol_ident) > 32:
            raise PyIsoException("The volume identifier has a maximum length of 32")
        self.volume_identifier = "{:<32}".format(vol_ident.encode('utf-16_be'))

        # The space_size is the number of extents (2048-byte blocks) in the
        # ISO.  We know we will at least have the system area (16 extents),
        # the PVD (1 extent), the Volume Terminator (2 extents), 2 extents
        # for the little endian path table record, 2 extents for the big endian
        # path table record, and 1 extent for the root directory record,
        # for a total of 24 extents to start with.
        self.space_size = 24
        self.set_size = set_size
        if seqnum > set_size:
            raise PyIsoException("Sequence number must be less than or equal to set size")
        self.seqnum = seqnum
        self.log_block_size = log_block_size
        # The path table size is in bytes, and is always at least 10 bytes
        # (for the root directory record).
        self.path_tbl_size = 10
        self.path_table_num_extents = ceiling_div(self.path_tbl_size, 4096) * 2
        # By default the Little Endian Path Table record starts at extent 19
        # (right after the Volume Terminator).
        self.path_table_location_le = 19
        # By default the Big Endian Path Table record starts at extent 21
        # (two extents after the Little Endian Path Table Record).
        self.path_table_location_be = 21
        # FIXME: we don't support the optional path table location right now
        self.optional_path_table_location_le = 0
        self.optional_path_table_location_be = 0
        self.root_dir_record = DirectoryRecord()
        self.root_dir_record.new_root(seqnum, self.log_block_size)

        if len(vol_set_ident) > 128:
            raise PyIsoException("The maximum length for the volume set identifier is 128")
        self.volume_set_identifier = "{:<128}".format(vol_set_ident.encode('utf-16_be'))

        self.publisher_identifier = pub_ident
        self.publisher_identifier.check_filename(True)

        self.preparer_identifier = preparer_ident
        self.preparer_identifier.check_filename(True)

        self.application_identifier = app_ident
        self.application_identifier.check_filename(True)

        self.copyright_file_identifier = "{:<37}".format(copyright_file.encode('utf-16_be'))
        self.abstract_file_identifier = "{:<37}".format(abstract_file.encode('utf-16_be'))
        self.bibliographic_file_identifier = "{:<37}".format(bibli_file.encode('utf-16_be'))

        # We make a valid volume creation and volume modification date here,
        # but they will get overwritten during writeout.
        now = time.time()
        self.volume_creation_date = VolumeDescriptorDate()
        self.volume_creation_date.new(now)
        self.volume_modification_date = VolumeDescriptorDate()
        self.volume_modification_date.new(now)
        self.volume_expiration_date = VolumeDescriptorDate()
        self.volume_expiration_date.new(vol_expire_date)
        self.volume_effective_date = VolumeDescriptorDate()
        self.volume_effective_date.new(now)
        self.file_structure_version = 1

        if len(app_use) > 512:
            raise PyIsoException("The maximum length for the application use is 512")
        self.application_use = "{:<512}".format(app_use)

        self.orig_extent_loc = None
        # This is wrong but will be set by reshuffle_extents
        self.new_extent_loc = 0

        self.escape_sequences = '%/E' # FIXME: we should allow the user to set this

        self.initialized = True

    def record(self):
        '''
        A method to generate a string representing this Supplementary Volume
        Descriptor.

        Parameters:
         None.
        Returns:
         A string representing this Supplementary Volume Descriptor.
        '''
        if not self.initialized:
            raise PyIsoException("This Supplementary Volume Descriptor is not yet initialized")

        now = time.time()

        vol_create_date = VolumeDescriptorDate()
        vol_create_date.new(now)

        vol_mod_date = VolumeDescriptorDate()
        vol_mod_date.new(now)

        return struct.pack(self.fmt, self.descriptor_type, self.identifier,
                           self.version, self.flags, self.system_identifier,
                           self.volume_identifier, 0, self.space_size,
                           swab_32bit(self.space_size), self.escape_sequences,
                           self.set_size, swab_16bit(self.set_size),
                           self.seqnum, swab_16bit(self.seqnum),
                           self.log_block_size, swab_16bit(self.log_block_size),
                           self.path_tbl_size, swab_32bit(self.path_tbl_size),
                           self.path_table_location_le, self.optional_path_table_location_le,
                           swab_32bit(self.path_table_location_be),
                           self.optional_path_table_location_be,
                           self.root_dir_record.record(),
                           self.volume_set_identifier,
                           self.publisher_identifier.record(),
                           self.preparer_identifier.record(),
                           self.application_identifier.record(),
                           self.copyright_file_identifier,
                           self.abstract_file_identifier,
                           self.bibliographic_file_identifier,
                           vol_create_date.record(),
                           vol_mod_date.record(),
                           self.volume_expiration_date.record(),
                           self.volume_effective_date.record(),
                           self.file_structure_version, 0,
                           self.application_use, '\x00'*653)

    def extent_location(self):
        '''
        A method to get this Supplementary Volume Descriptor's extent location.

        Parameters:
         None.
        Returns:
         Integer of this Supplementary Volume Descriptor's extent location.
        '''
        if not self.initialized:
            raise PyIsoException("This Supplementary Volume Descriptor is not yet initialized")

        if self.new_extent_loc is None:
            return self.orig_extent_loc
        return self.new_extent_loc

class PathTableRecord(object):
    '''
    A class that represents a single ISO9660 Path Table Record.
    '''
    FMT = "=BBLH"

    def __init__(self):
        self.initialized = False

    def parse(self, data):
        '''
        Parse an ISO9660 Path Table Record out of a string.

        Parameters:
         data - The string to parse.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("Path Table Record already initialized")

        (self.len_di, self.xattr_length, self.extent_location,
         self.parent_directory_num) = struct.unpack(self.FMT, data[:8])

        if self.len_di % 2 != 0:
            self.directory_identifier = data[8:-1]
        else:
            self.directory_identifier = data[8:]
        self.dirrecord = None
        if self.directory_identifier == '\x00':
            # For the root path table record, it's own directory num is 1
            self.directory_num = 1
        else:
            self.directory_num = self.parent_directory_num + 1
        self.initialized = True

    def _record(self, ext_loc, parent_dir_num):
        '''
        An internal method to generate a string representing this Path Table Record.

        Parameters:
         ext_loc - The extent location to place in this Path Table Record.
         parent_dir_num - The parent directory number to place in this Path Table
                          Record.
        Returns:
         A string representing this Path Table Record.
        '''
        return struct.pack(self.FMT, self.len_di, self.xattr_length,
                           ext_loc, parent_dir_num) + self.directory_identifier + '\x00'*(self.len_di % 2)

    def record_little_endian(self):
        '''
        A method to generate a string representing the little endian version of
        this Path Table Record.

        Parameters:
         None.
        Returns:
         A string representing the little endian version of this Path Table Record.
        '''
        if not self.initialized:
            raise PyIsoException("Path Table Record not yet initialized")

        return self._record(self.extent_location, self.parent_directory_num)

    def record_big_endian(self):
        '''
        A method to generate a string representing the big endian version of
        this Path Table Record.

        Parameters:
         None.
        Returns:
         A string representing the big endian version of this Path Table Record.
        '''
        if not self.initialized:
            raise PyIsoException("Path Table Record not yet initialized")

        return self._record(swab_32bit(self.extent_location),
                            swab_16bit(self.parent_directory_num))

    @classmethod
    def record_length(cls, len_di):
        '''
        A class method to calculate the length of this Path Table Record.
        '''
        # This method can be called even if the object isn't initialized
        return struct.calcsize(cls.FMT) + len_di + (len_di % 2)

    def _new(self, name, dirrecord, parent_dir_num):
        '''
        An internal method to create a new Path Table Record.

        Parameters:
         name - The name for this Path Table Record.
         dirrecord - The directory record to associate with this Path Table Record.
         parent_dir_num - The directory number of the parent of this Path Table
                          Record.
        Returns:
         Nothing.
        '''
        self.len_di = len(name)
        self.xattr_length = 0 # FIXME: we don't support xattr for now
        self.extent_location = 0
        self.parent_directory_num = parent_dir_num
        self.directory_identifier = name
        self.dirrecord = dirrecord
        if self.directory_identifier == '\x00':
            # For the root path table record, it's own directory num is 1
            self.directory_num = 1
        else:
            self.directory_num = self.parent_directory_num + 1
        self.initialized = True

    def new_root(self, dirrecord):
        '''
        A method to create a new root Path Table Record.

        Parameters:
         dirrecord - The directory record to associate with this Path Table Record.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("Path Table Record already initialized")

        self._new("\x00", dirrecord, 1)

    def new_dir(self, name, dirrecord, parent_dir_num):
        '''
        A method to create a new Path Table Record.

        Parameters:
         name - The name for this Path Table Record.
         dirrecord - The directory record to associate with this Path Table Record.
         parent_dir_num - The directory number of the parent of this Path Table
                          Record.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("Path Table Record already initialized")

        self._new(name, dirrecord, parent_dir_num)

    def set_dirrecord(self, dirrecord):
        '''
        A method to set the directory record associated with this Path Table
        Record.

        Parameters:
         dirrecord - The directory record to associate with this Path Table Record.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("Path Table Record not yet initialized")

        self.dirrecord = dirrecord

    def update_extent_location_from_dirrecord(self):
        '''
        A method to update the extent location for this Path Table Record from
        the corresponding directory record.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("Path Table Record not yet initialized")

        self.extent_location = self.dirrecord.extent_location()

    def __lt__(self, other):
        return ptr_lt(self.directory_identifier, other.directory_identifier)

def ptr_lt(str1, str2):
    '''
    A function to compare two identifiers according to hte ISO9660 Path Table Record
    sorting order.

    Parameters:
     str1 - The first identifier.
     str2 - The second identifier.
    Returns:
     True if str1 is less than or equal to str2, False otherwise.
    '''
    # This method is used for the bisect.insort_left() when adding a child.
    # It needs to return whether str1 is less than str2.  Here we use the
    # ISO9660 sorting order which is essentially:
    #
    # 1.  The \x00 is always the "dot" record, and is always first.
    # 2.  The \x01 is always the "dotdot" record, and is always second.
    # 3.  Other entries are sorted lexically; this does not exactly match
    #     the sorting method specified in Ecma-119, but does OK for now.
    #
    # FIXME: we need to implement Ecma-119 section 9.3 for the sorting
    # order.
    if str1 == '\x00':
        # If both str1 and str2 are 0, then they are not strictly less.
        if str2 == '\x00':
            return False
        return True
    if str2 == '\x00':
        return False

    if str1 == '\x01':
        if str2 == '\x00':
            return False
        return True

    if str2 == '\x01':
        # If str1 was '\x00', it would have been caught above.
        return False
    return str1 < str2

def swab_32bit(input_int):
    '''
    A function to swab a 32-bit integer.

    Parameters:
     input_int - The 32-bit integer to swab.
    Returns:
     The swabbed version of the 32-bit integer.
    '''
    return socket.htonl(input_int)

def swab_16bit(input_int):
    '''
    A function to swab a 16-bit integer.

    Parameters:
     input_int - The 16-bit integer to swab.
    Returns:
     The swabbed version of the 16-bit integer.
    '''
    return socket.htons(input_int)

def pad(data_size, pad_size):
    '''
    A function to generate a string of padding zeros, if necessary.  Given the
    current data_size, and a target pad_size, this function will generate a string
    of zeros that will take the data_size up to the pad size.

    Parameters:
     data_size - The current size of the data.
     pad_size - The desired pad size.
    Returns:
     String containing the zero padding.
    '''
    padbytes = pad_size - (data_size % pad_size)
    if padbytes != pad_size:
        return "\x00" * padbytes
    return ""

def gmtoffset_from_tm(tm, local):
    '''
    A function to compute the GMT offset from the time in seconds since the epoch
    and the local time object.

    Parameters:
     tm - The time in seconds since the epoch.
     local - The struct_time object representing the local time.
    Returns:
     The gmtoffset.
    '''
    gmtime = time.gmtime(tm)
    tmpyear = gmtime.tm_year - local.tm_year
    tmpyday = gmtime.tm_yday - local.tm_yday
    tmphour = gmtime.tm_hour - local.tm_hour
    tmpmin = gmtime.tm_min - local.tm_min

    if tmpyday < 0:
        tmpyday = -1
    else:
        if tmpyear > 0:
            tmpyday = 1
    return -(tmpmin + 60 * (tmphour + 24 * tmpyday)) / 15

def ceiling_div(numer, denom):
    '''
    A function to do ceiling division; that is, dividing numerator by denominator
    and taking the ceiling.

    Parameters:
     numer - The numerator for the division.
     denom - The denominator for the division.
    Returns:
     The ceiling after dividing numerator by denominator.
    '''
    # Doing division and then getting the ceiling is tricky; we do upside-down
    # floor division to make this happen.
    # See https://stackoverflow.com/questions/14822184/is-there-a-ceiling-equivalent-of-operator-in-python.
    return -(-numer // denom)

def check_d1_characters(name):
    '''
    A function to check that a name only uses d1 characters as defined by ISO9660.

    Parameters:
     name - The name to check.
    Returns:
     Nothing.
    '''
    for char in name:
        if not char in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K',
                        'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V',
                        'W', 'X', 'Y', 'Z', '0', '1', '2', '3', '4', '5', '6',
                        '7', '8', '9', '_', '.', '-', '+', '(', ')', '~', '&',
                        '!', '@', '$']:
            raise PyIsoException("%s is not a valid ISO9660 filename (it contains invalid characters)" % (name))

def check_iso9660_filename(fullname, interchange_level):
    '''
    A function to check that a file identifier conforms to the ISO9660 rules
    for a particular interchange level.

    Parameters:
     fullname - The name to check.
     interchange_level - The interchange level to check against.
    Returns:
     Nothing.
    '''
    # Check to ensure the name is a valid filename for the ISO according to
    # Ecma-119 7.5.
    # First we split on the semicolon for the version number.
    namesplit = fullname.split(';')

    # Ecma-119 says that filenames must end with a semicolon-number, but I have
    # found CDs (Ubuntu 14.04 Desktop i386, for instance) that do not follow
    # this.  Thus we allow for names both with and without the semi+version.
    if len(namesplit) == 2:
        version = namesplit[1]

        # The second entry should be the version number between 1 and 32767.
        if int(version) < 1 or int(version) > 32767:
            raise PyIsoException("%s has an invalid version number (must be between 1 and 32767" % (fullname))
    elif len(namesplit) != 1:
        raise PyIsoException("%s contains multiple semicolons!" % (fullname))

    name_plus_extension = namesplit[0]

    # The first entry should be x.y, so we split on the dot.
    dotsplit = name_plus_extension.split('.')
    if len(dotsplit) == 1:
        name = dotsplit[0]
        extension = ''
    else:
        name = '.'.join(dotsplit[:-1])
        extension = dotsplit[-1]

    # Ecma-119 section 7.5.1 specifies that filenames must have at least one
    # character in either the name or the extension.
    if len(name) == 0 and len(extension) == 0:
        raise PyIsoException("%s is not a valid ISO9660 filename (either the name or extension must be non-empty" % (fullname))

    if interchange_level == 1:
        # According to Ecma-119, section 10.1, at level 1 the filename can
        # only be up to 8 d-characters or d1-characters, and the extension can
        # only be up to 3 d-characters or 3 d1-characters.
        if len(name) > 8 or len(extension) > 3:
            raise PyIsoException("%s is not a valid ISO9660 filename at interchange level 1" % (fullname))
    else:
        # For all other interchange levels, the maximum filename length is
        # specified in Ecma-119 7.5.2.  However, I have found CDs (Ubuntu 14.04
        # Desktop i386, for instance) that don't conform to this.  Skip the
        # check until we know how long is allowed.
        pass

    # Ecma-119 section 7.5.1 says that the file name and extension each contain
    # zero or more d-characters or d1-characters.  While the definition of
    # d-characters and d1-characters is not specified in Ecma-119,
    # http://wiki.osdev.org/ISO_9660 suggests that this consists of A-Z, 0-9, _
    # which seems to correlate with empirical evidence.  Thus we check for that
    # here.
    check_d1_characters(name.upper())
    check_d1_characters(extension.upper())

def check_iso9660_directory(fullname, interchange_level):
    '''
    A function to check that an directory identifier conforms to the ISO9660 rules
    for a particular interchange level.

    Parameters:
     fullname - The name to check.
     interchange_level - The interchange level to check against.
    Returns:
     Nothing.
    '''
    # Check to ensure the directory name is valid for the ISO according to
    # Ecma-119 7.6.

    # Ecma-119 section 7.6.1 says that a directory identifier needs at least one
    # character
    if len(fullname) < 1:
        raise PyIsoException("%s is not a valid ISO9660 directory name (the name must have at least 1 character long)" % (fullname))

    if interchange_level == 1:
        # Ecma-119 section 10.1 says that directory identifiers lengths cannot
        # exceed 8 at interchange level 1.
        if len(fullname) > 8:
            raise PyIsoException("%s is not a valid ISO9660 directory name at interchange level 1" % (fullname))
    else:
        # Ecma-119 section 7.6.3 says that directory identifiers lengths cannot
        # exceed 31.
        if len(fullname) > 207:
            raise PyIsoException("%s is not a valid ISO9660 directory name (it is longer than 31 characters)" % (fullname))

    # Ecma-119 section 7.6.1 says that directory names consist of one or more
    # d-characters or d1-characters.  While the definition of d-characters and
    # d1-characters is not specified in Ecma-119,
    # http://wiki.osdev.org/ISO_9660 suggests that this consists of A-Z, 0-9, _
    # which seems to correlate with empirical evidence.  Thus we check for that
    # here.
    check_d1_characters(fullname.upper())

def check_interchange_level(identifier, is_dir):
    '''
    A function to determine the interchange level of an identifier on an ISO.
    Since ISO9660 doesn't encode the interchange level on the ISO itself,
    this is used to infer the interchange level of an ISO.

    Parameters:
     identifier - The identifier to figure out the interchange level for.
     is_dir - Whether this is a directory or a file.
    Returns:
     The interchange level as an integer.
    '''
    interchange_level = 1
    cmpfunc = check_iso9660_filename
    if is_dir:
        cmpfunc = check_iso9660_directory

    try_level_3 = False
    try:
        # First we try to check for interchange level 1; if
        # that fails, we fall back to interchange level 3
        # and check that.
        cmpfunc(identifier, 1)
    except PyIsoException:
        try_level_3 = True

    if try_level_3:
        cmpfunc(identifier, 3)
        # If the above did not throw an exception, then this
        # is interchange level 3 and we should mark it.
        interchange_level = 3

    return interchange_level

def copy_data(data_length, blocksize, infp, outfp):
    '''
    A utility function to copy data from the input file object to the output
    file object.  This function will use the most efficient copy method available,
    which is often sendfile.

    Parameters:
     data_length - The amount of data to copy.
     blocksize - How much data to copy per iteration.
     infp - The file object to copy data from.
     outfp - The file object to copy data to.
    Returns:
     Nothing.
    '''
    if hasattr(infp, 'fileno') and hasattr(outfp, 'fileno'):
        # This is one of those instances where using the file object and the
        # file descriptor causes problems.  The sendfile() call actually updates
        # the underlying file descriptor, but the file object does not know
        # about it.  To get around this, we instead get the offset, allow
        # sendfile() to update the offset, then manually seek the file object
        # to the right location.  This ensures that the file object gets updated
        # properly.
        in_offset = infp.tell()
        out_offset = outfp.tell()
        sendfile.sendfile(outfp.fileno(), infp.fileno(), in_offset, data_length)
        infp.seek(in_offset + data_length)
        outfp.seek(out_offset + data_length)
    else:
        left = data_length
        readsize = blocksize
        while left > 0:
            if left < readsize:
                readsize = left
            outfp.write(infp.read(readsize))
            left -= readsize

def hexdump(st):
    '''
    A utility function to print a string in hex.

    Parameters:
     st - The string to print.
    Returns:
     A string containing the hexadecimal representation of the input string.
    '''
    return ':'.join(x.encode('hex') for x in st)

class IsoHybrid(object):
    '''
    A class that represents an ISO hybrid; that is, an ISO that can be booted via
    CD or via an alternate boot mechanism (such as USB).
    '''
    def __init__(self):
        self.fmt = "=432sLLLH"
        self.initialized = False

    def parse(self, instr):
        '''
        A method to parse ISO hybridization info out of an existing ISO.

        Parameters:
         instr - The data for the ISO hybridization.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("This IsoHybrid object is already initialized")

        if len(instr) != 512:
            raise PyIsoException("Invalid size of the instr")

        (self.mbr, self.rba, unused1, self.mbr_id, unused2) = struct.unpack(self.fmt, instr[:struct.calcsize(self.fmt)])

        if unused1 != 0:
            raise PyIsoException("Invalid IsoHybrid section")

        if unused2 != 0:
            raise PyIsoException("Invalid IsoHybrid section")

        offset = struct.calcsize(self.fmt)
        self.part_entry = None
        for i in range(1, 5):
            if instr[offset] == '\x80':
                self.part_entry = i
                (const, self.bhead, self.bsect, self.bcyle, self.ptype,
                 self.ehead, self.esect, self.ecyle, self.part_offset,
                 self.psize) = struct.unpack("=BBBBBBBBLL", instr[offset:offset+16])
                break
            offset += 16

        if self.part_entry is None:
            raise PyIsoException("No valid partition found in IsoHybrid!")

        if instr[-2] != '\x55' or instr[-1] != '\xaa':
            raise PyIsoException("Invalid tail on isohybrid section")

        self.geometry_heads = self.ehead + 1
        # FIXME: I can't see anyway to compute the number of sectors from the
        # available information.  For now, we just hard-code this at 32 and
        # hope for the best.
        self.geometry_sectors = 32

        self.initialized = True

    def new(self, instr, rba, part_entry, mbr_id, part_offset,
            geometry_sectors, geometry_heads, part_type):
        '''
        A method to add ISO hybridization to an ISO.

        Parameters:
         instr - The data to be put into the MBR.
         rba - The address at which to put the data.
         part_entry - The partition entry for the hybridization.
         mbr_id - The mbr_id to use for the hybridization.
         part_offset - The partition offset to use for the hybridization.
         geometry_sectors - The number of sectors to use for the hybridization.
         geometry_heads - The number of heads to use for the hybridization.
         part_type - The partition type for the hybridization.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("This IsoHybrid object is already initialized")

        self.mbr = instr
        self.rba = rba
        self.mbr_id = mbr_id
        if self.mbr_id is None:
            self.mbr_id = random.getrandbits(32)

        self.part_entry = part_entry
        self.bhead = (part_offset / geometry_sectors) % geometry_heads
        self.bsect = (part_offset % geometry_sectors) + 1
        self.bcyle = part_offset / (geometry_heads * geometry_sectors)
        self.bsect += (self.bcyle & 0x300) >> 2
        self.bcyle &= 0xff
        self.ptype = part_type
        self.ehead = geometry_heads - 1
        self.part_offset = part_offset
        self.geometry_heads = geometry_heads
        self.geometry_sectors = geometry_sectors

        self.initialized = True

    def _calc_cc(self, iso_size):
        '''
        A method to calculate the "cc" and the "padding" values for this
        hybridization.

        Parameters:
         iso_size - The size of the ISO, excluding the hybridization.
        Returns:
         A tuple containing the cc value and the padding.
        '''
        cylsize = self.geometry_heads * self.geometry_sectors * 512
        frac = iso_size % cylsize
        padding = 0
        if frac > 0:
            padding = cylsize - frac
        cc = (iso_size + padding) / cylsize
        if cc > 1024:
            cc = 1024

        return (cc,padding)

    def record(self, iso_size):
        '''
        A method to generate a string containing the ISO hybridization.

        Parameters:
         iso_size - The size of the ISO, excluding the hybridization.
        Returns:
         A string containing the ISO hybridization.
        '''
        if not self.initialized:
            raise PyIsoException("This IsoHybrid object is not yet initialized")

        ret = struct.pack("=432sLLLH", self.mbr, self.rba, 0, self.mbr_id, 0)

        for i in range(1, 5):
            if i == self.part_entry:
                cc,padding = self._calc_cc(iso_size)
                esect = self.geometry_sectors + (((cc - 1) & 0x300) >> 2)
                ecyle = (cc - 1) & 0xff
                psize = cc * self.geometry_heads * self.geometry_sectors - self.part_offset
                ret += struct.pack("=BBBBBBBBLL", 0x80, self.bhead, self.bsect,
                                   self.bcyle, self.ptype, self.ehead,
                                   esect, ecyle, self.part_offset, psize)
            else:
                ret += '\x00'*16
        ret += '\x55\xaa'

        return ret

    def record_padding(self, iso_size):
        '''
        A method to record padding for the ISO hybridization.

        Parameters:
         iso_size - The size of the ISO, excluding the hybridization.
        Returns:
         A string of zeros the right size to pad the ISO.
        '''
        if not self.initialized:
            raise PyIsoException("This IsoHybrid object is not yet initialized")

        return '\x00'*self._calc_cc(iso_size)[1]

class VersionVolumeDescriptor(object):
    '''
    A class representing a Version Volume Descriptor.  This volume descriptor is
    not mentioned in any of the standards, but is included by genisoimage, so it
    is modeled here.
    '''
    def __init__(self):
        self.orig_extent_loc = None
        self.new_extent_loc = None
        self.initialized = False

    def parse(self, extent_location):
        '''
        Do a "parse" of a Version Volume Descriptor.  This consists of just setting
        the extent location of the Version Volume Descriptor properly.

        Parameters:
         extent_location - The location of the extent on the original ISO of this
                           Version Volume Descriptor.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("This Version Volume Descriptor is already initialized")

        self.orig_extent_loc = extent_location
        self.initialized = True

    def new(self):
        '''
        Create a new Version Volume Descriptor.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("This Version Volume Descriptor is already initialized")

        self.initialized = True

    def record(self, log_block_size):
        '''
        Generate a string representing this Version Volume Descriptor.  Note that
        right now, this is always a string of zeros.

        Parameters:
         log_block_size - The logical block size to use when generating this string.
        Returns:
         A string representing this Version Volume Descriptor.
        '''
        if not self.initialized:
            raise PyIsoException("This Version Volume Descriptor is not yet initialized")

        return "\x00" * log_block_size

    def extent_location(self):
        '''
        Get the extent location of this Version Volume Descriptor.

        Parameters:
         None.
        Returns:
         An integer representing the extent location of this Version Volume
         Descriptor.
        '''
        if not self.initialized:
            raise PyIsoException("This Version Volume Descriptor is not yet initialized")

        if self.new_extent_loc is not None:
            return self.new_extent_loc
        return self.orig_extent_loc

class PyIso(object):
    '''
    The main class for manipulating ISOs.
    '''
    def _parse_volume_descriptors(self):
        '''
        An internal method to parse the volume descriptors on an ISO.

        Parameters:
         None.
        Returns:
         A tuple containing the PVDs, SVDs, VPDs, BRs, and VDSTs on the ISO.
        '''
        # Ecma-119 says that the Volume Descriptor set is a sequence of volume
        # descriptors recorded in consecutively numbered Logical Sectors
        # starting with Logical Sector Number 16.  Since sectors are 2048 bytes
        # in length, we start at sector 16 * 2048
        pvds = []
        vdsts = []
        brs = []
        svds = []
        vpds = []
        # Ecma-119, 6.2.1 says that the Volume Space is divided into a System
        # Area and a Data Area, where the System Area is in logical sectors 0
        # to 15, and whose contents is not specified by the standard.
        self.cdfp.seek(16 * 2048)
        done = False
        while not done:
            # All volume descriptors are exactly 2048 bytes long
            curr_extent = self.cdfp.tell() / 2048
            vd = self.cdfp.read(2048)
            (desc_type,) = struct.unpack("=B", vd[0])
            if desc_type == VOLUME_DESCRIPTOR_TYPE_PRIMARY:
                pvd = PrimaryVolumeDescriptor()
                pvd.parse(vd, self.cdfp, 16)
                pvds.append(pvd)
            elif desc_type == VOLUME_DESCRIPTOR_TYPE_SET_TERMINATOR:
                vdst = VolumeDescriptorSetTerminator()
                vdst.parse(vd, curr_extent)
                vdsts.append(vdst)
                # Once we see a set terminator, we stop parsing.  Oddly,
                # Ecma-119 says there may be multiple set terminators, but in
                # that case I don't know how to tell when we are done parsing
                # volume descriptors.  Leave this for now.
                done = True
            elif desc_type == VOLUME_DESCRIPTOR_TYPE_BOOT_RECORD:
                br = BootRecord()
                br.parse(vd, curr_extent)
                brs.append(br)
            elif desc_type == VOLUME_DESCRIPTOR_TYPE_SUPPLEMENTARY:
                svd = SupplementaryVolumeDescriptor()
                svd.parse(vd, self.cdfp, curr_extent)
                svds.append(svd)
            elif desc_type == VOLUME_DESCRIPTOR_TYPE_VOLUME_PARTITION:
                raise PyIsoException("Unimplemented Volume Partition descriptor!")
            else:
                raise PyIsoException("Invalid volume descriptor type %d" % (desc_type))
        return pvds, svds, vpds, brs, vdsts

    def _seek_to_extent(self, extent):
        '''
        An internal method to seek to a particular extent on the input ISO.

        Parameters:
         extent - The extent to seek to.
        Returns:
         Nothing.
        '''
        self.cdfp.seek(extent * self.pvd.logical_block_size())

    def _walk_directories(self, vd, do_check_interchange):
        '''
        An internal method to walk the directory records in a volume descriptor,
        starting with the root.  For each child in the directory record,
        we create a new DirectoryRecord object and append it to the parent.

        Parameters:
         vd - The volume descriptor to walk.
         do_check_interchange - Whether to check the interchange level or not.
        Returns:
         The interchange level that this ISO conforms to.
        '''
        vd.set_ptr_dirrecord(vd.root_directory_record())
        interchange_level = 1
        dirs = collections.deque([vd.root_directory_record()])
        block_size = vd.logical_block_size()
        while dirs:
            dir_record = dirs.popleft()

            self._seek_to_extent(dir_record.extent_location())
            length = dir_record.file_length()
            while length > 0:
                # read the length byte for the directory record
                (lenbyte,) = struct.unpack("=B", self.cdfp.read(1))
                length -= 1
                if lenbyte == 0:
                    # If we saw zero length, this may be a padding byte; seek
                    # to the start of the next extent.
                    if length > 0:
                        padsize = block_size - (self.cdfp.tell() % block_size)
                        padbytes = self.cdfp.read(padsize)
                        if padbytes != '\x00'*padsize:
                            # For now we are pedantic, and if the padding bytes
                            # are not all zero we throw an Exception.  Depending
                            # one what we see in the wild, we may have to loosen
                            # this check.
                            raise PyIsoException("Invalid padding on ISO")
                        length -= padsize
                        if length < 0:
                            # For now we are pedantic, and if the length goes
                            # negative because of the padding we throw an
                            # exception.  Depending on what we see in the wild,
                            # we may have to loosen this check.
                            raise PyIsoException("Invalid padding on ISO")
                    continue
                new_record = DirectoryRecord()
                self.rock_ridge |= new_record.parse(struct.pack("=B", lenbyte) + self.cdfp.read(lenbyte - 1),
                                                    self.cdfp, dir_record,
                                                    self.pvd.logical_block_size())

                if new_record.rock_ridge is not None and new_record.rock_ridge.ce_record is not None:
                    orig_pos = self.cdfp.tell()
                    self._seek_to_extent(new_record.rock_ridge.ce_record.continuation_entry.extent_location())
                    self.cdfp.seek(new_record.rock_ridge.ce_record.continuation_entry.offset(), os.SEEK_CUR)
                    con_block = self.cdfp.read(new_record.rock_ridge.ce_record.continuation_entry.length())
                    new_record.rock_ridge.ce_record.continuation_entry.parse(con_block,
                                                                             new_record.rock_ridge.bytes_to_skip)
                    self.cdfp.seek(orig_pos)

                if isinstance(vd, PrimaryVolumeDescriptor) and self.eltorito_boot_catalog is not None:
                    if new_record.extent_location() == self.eltorito_boot_catalog.extent_location():
                        self.eltorito_boot_catalog.set_dirrecord(new_record)
                    elif new_record.extent_location() == self.eltorito_boot_catalog.initial_entry.load_rba:
                        self.eltorito_boot_catalog.set_initial_entry_dirrecord(new_record)

                length -= lenbyte - 1
                if new_record.is_dir():
                    if not new_record.is_dot() and not new_record.is_dotdot():
                        if do_check_interchange:
                            interchange_level = max(interchange_level, check_interchange_level(new_record.file_identifier(), True))
                        dirs.append(new_record)
                        vd.set_ptr_dirrecord(new_record)
                else:
                    if do_check_interchange:
                        interchange_level = max(interchange_level, check_interchange_level(new_record.file_identifier(), False))
                dir_record.add_child(new_record, vd, True)

        return interchange_level

    def _initialize(self):
        '''
        An internal method to re-initialize the object.  Called from
        both __init__ and close.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        self.cdfp = None
        self.pvd = None
        self.svds = []
        self.vpds = []
        self.brs = []
        self.vdsts = []
        self.eltorito_boot_catalog = None
        self.initialized = False
        self.rock_ridge = False
        self.isohybrid_mbr = None

    def _parse_path_table(self, vd, extent, callback):
        '''
        An internal method to parse a path table on an ISO.  For each path
        table entry found, a Path Table Record object is created, and the
        callback is called.

        Parameters:
         vd - The volume descriptor that these path table records correspond to.
         extent - The extent at which this path table record starts.
         callback - The callback to call for each path table record.
        Returns:
         Nothing.
        '''
        self._seek_to_extent(extent)
        left = vd.path_table_size()
        while left > 0:
            ptr = PathTableRecord()
            (len_di,) = struct.unpack("=B", self.cdfp.read(1))
            read_len = PathTableRecord.record_length(len_di)
            # PathTableRecord.record_length() returns the length of the entire
            # path table record, but we've already read the len_di so read one
            # less.
            ptr.parse(struct.pack("=B", len_di) + self.cdfp.read(read_len - 1))
            left -= read_len
            callback(vd, ptr)

    def _little_endian_path_table(self, vd, ptr):
        '''
        The callback that is used when parsing the little-endian path tables.
        In this case, we actually store the path table record inside the
        passed in Volume Descriptor.

        Parameters:
         vd - The volume descriptor that this callback is for.
         ptr - A Path Table Record object.
        Returns:
         Nothing.
        '''
        vd.add_path_table_record(ptr)

    def _big_endian_path_table(self, vd, ptr):
        '''
        The callback that is used when parsing the big-endian path tables.
        In this case, we store the path table record inside a temporary list
        of path table records; it will eventually be used to ensure consistency
        between the big-endian and little-endian path tables.

        Parameters:
         vd - The volume descriptor that this callback is for.
         ptr - A Path Table Record object.
        Returns:
         Nothing.
        '''
        bisect.insort_left(self.tmp_be_path_table_records, ptr)

    def _find_record(self, vd, path, encoding='ascii'):
        '''
        An internal method to find an entry on the ISO given a Volume
        Descriptor, a full ISO path, and an encoding.  Once the entry is found,
        return the directory record object corresponding to that entry, as well
        as the index within the list of children for that particular parent.
        If the entry could not be found, a PyIsoException is raised.

        Parameters:
         vd - The volume descriptor in which to look up the entry.
         path - The absolute path to look up in the volume descriptor.
         encoding - The encoding to use on the individual portions of the path.
        Returns:
         A tuple containing a directory record entry representing the entry on
         the ISO and the index of that entry into the parent's child list.
        '''
        if path[0] != '/':
            raise PyIsoException("Must be a path starting with /")

        # If the path is just the slash, we just want the root directory, so
        # get the children there and quit.
        if path == '/':
            return vd.root_directory_record(),0

        # Split the path along the slashes
        splitpath = path.split('/')
        # Skip past the first one, since it is always empty.
        splitindex = 1

        currpath = splitpath[splitindex].encode(encoding)
        splitindex += 1
        children = vd.root_directory_record().children
        index = 0
        while index < len(children):
            child = children[index]
            index += 1

            if child.is_dot() or child.is_dotdot():
                continue

            if child.file_identifier() != currpath:
                if child.rock_ridge is None:
                    continue

                if child.rock_ridge.name() != currpath:
                    continue

            # We found the child, and it is the last one we are looking for;
            # return it.
            if splitindex == len(splitpath):
                # We have to remove one from the index since we incremented it
                # above.
                return child,index-1
            else:
                if child.is_dir():
                    children = child.children
                    index = 0
                    currpath = splitpath[splitindex].encode(encoding)
                    splitindex += 1

        raise PyIsoException("Could not find path %s" % (path))

    def _internal_name_and_parent_from_path(self, iso_path, vd):
        '''
        An internal method to find the parent directory record given a full
        ISO path and a Volume Descriptor.  If the parent is found, return the
        parent directory record object and the relative path of the original
        path.

        Parameters:
         iso_path - The absolute path to the entry on the ISO.
         vd - The volume descriptor in which to look up the entry.
        Returns:
         A tuple containing just the name of the entry and a Directory Record
         object representing the parent of the entry.
        '''
        if iso_path[0] != '/':
            raise PyIsoException("Must be a path starting with /")

        # First we need to find the parent of this directory, and add this
        # one as a child.
        splitpath = iso_path.split('/')
        # Pop off the front, as it is always blank.
        splitpath.pop(0)
        if isinstance(vd, PrimaryVolumeDescriptor) and len(splitpath) > 7:
            # Ecma-119 Section 6.8.2.1 says that the number of levels in the
            # hierarchy shall not exceed eight.  However, since the root
            # directory must always reside at level 1 by itself, this gives us
            # an effective maximum hierarchy depth of 7.
            raise PyIsoException("Directory levels too deep (maximum is 7)")
        # Now take the name off.
        name = splitpath.pop()
        if len(splitpath) == 0:
            # This is a new directory under the root, add it there
            parent = vd.root_directory_record()
        else:
            parent,index = self._find_record(vd, '/' + '/'.join(splitpath))

        return (name, parent)

    def _name_and_parent_from_path(self, iso_path):
        '''
        An internal method to find the parent directory record in the Primary
        Volume Descriptor of a full ISO path.  If the parent is found, return
        the parent directory record object and the relative path of the
        original path.

        Parameters:
         iso_path - The absolute path to the entry on the ISO.
        Returns:
         A tuple containing just the name of the entry and a Directory Record
         object representing the parent of the entry.
        '''
        return self._internal_name_and_parent_from_path(iso_path, self.pvd)

    def _joliet_name_and_parent_from_path(self, joliet_path):
        '''
        An internal method to find the parent directory record in the Joliet
        Volume Descriptor of a full ISO path.  If the parent is found, return
        the parent directory record object and the relative path of the
        original path.

        Parameters:
         joliet_path - The absolute path to the Joliet entry on the ISO.
        Returns:
         A tuple containing just the name of the entry and a Directory Record
         object representing the parent of the entry.
        '''
        return self._internal_name_and_parent_from_path(joliet_path, self.joliet_vd)

    def _check_and_parse_eltorito(self, br, logical_block_size):
        '''
        An internal method to examine a Boot Record and see if it is an
        El Torito Boot Record.  If it is, parse the El Torito Boot Catalog,
        verification entry, initial entry, and any additional section entries.

        Parameters:
         br - The boot record to examine for an El Torito signature.
         logical_block_size - The logical block size of the ISO.
        Returns:
         Nothing.
        '''
        if br.boot_system_identifier != "{:\x00<32}".format("EL TORITO SPECIFICATION"):
            return

        if self.eltorito_boot_catalog is not None:
            raise PyIsoException("Only one El Torito boot record is allowed")

        # According to the El Torito specification, section 2.0, the El
        # Torito boot record must be at extent 17.
        if br.extent_location() != 17:
            raise PyIsoException("El Torito Boot Record must be at extent 17")

        # Now that we have verified that the BootRecord is an El Torito one
        # and that it is sane, we go on to parse the El Torito Boot Catalog.
        # Note that the Boot Catalog is stored as a file in the ISO, though
        # we ignore that for the purposes of parsing.

        self.eltorito_boot_catalog = EltoritoBootCatalog(br)
        eltorito_boot_catalog_extent, = struct.unpack("=L", br.boot_system_use[:4])

        old = self.cdfp.tell()
        self.cdfp.seek(eltorito_boot_catalog_extent * logical_block_size)
        data = self.cdfp.read(32)
        while not self.eltorito_boot_catalog.parse(data):
            data = self.cdfp.read(32)
        self.cdfp.seek(old)

    def _reassign_vd_dirrecord_extents(self, vd, current_extent):
        '''
        An internal helper method for reassign_extents that assigns extents to
        directory records for the passed in Volume Descriptor.  The current
        extent is passed in, and this function returns the extent after the
        last one it assigned.

        Parameters:
         vd - The volume descriptor on which to operate.
         current_extent - The current extent before assigning extents to the
                          volume descriptor directory records.
        Returns:
         The current extent after assigning extents to the volume descriptor
         directory records.
        '''
        # Here we re-walk the entire tree, re-assigning extents as necessary.
        root_dir_record = vd.root_directory_record()
        root_dir_record.update_location(current_extent)
        # Equivalent to ceiling_div(root_dir_record.data_length, self.pvd.log_block_size), but faster
        current_extent += -(-root_dir_record.data_length // vd.log_block_size)

        rr_cont_extent = None
        rr_cont_offset = 0

        # Walk through the list, assigning extents to all of the directories.
        dirs = collections.deque([root_dir_record])
        while dirs:
            dir_record = dirs.popleft()
            for child in dir_record.children:
                # Equivalent to child.is_dot(), but faster.
                if child.isdir and child.file_ident == '\x00':
                    child.new_extent_loc = child.parent.extent_location()
                # Equivalent to child.is_dotdot(), but faster.
                elif child.isdir and child.file_ident == '\x01':
                    if child.parent.is_root:
                        # Special case of the root directory record.  In this
                        # case, we assume that the dot record has already been
                        # added, and is the one before us.  We set the dotdot
                        # extent location to the same as the dot one.
                        child.new_extent_loc = child.parent.extent_location()
                    else:
                        child.new_extent_loc = child.parent.parent.extent_location()
                else:
                    if child.isdir:
                        child.new_extent_loc = current_extent
                        # Equivalent to ceiling_div(child.data_length, vd.log_block_size), but faster
                        current_extent += -(-child.data_length // vd.log_block_size)
                        dirs.append(child)
                    if child.rock_ridge is not None and child.rock_ridge.ce_record is not None:
                        rr_cont_len = child.rock_ridge.ce_record.continuation_entry.length()
                        if rr_cont_extent is None or ((vd.log_block_size - rr_cont_offset) < rr_cont_len):
                            child.rock_ridge.ce_record.continuation_entry.new_extent_loc = current_extent
                            child.rock_ridge.ce_record.continuation_entry.continue_offset = 0
                            rr_cont_extent = current_extent
                            rr_cont_offset = rr_cont_len
                            current_extent += 1
                        else:
                            child.rock_ridge.ce_record.continuation_entry.new_extent_loc = rr_cont_extent
                            child.rock_ridge.ce_record.continuation_entry.continue_offset = rr_cont_offset
                            rr_cont_offset += rr_cont_len

        # After we have reshuffled the extents we need to update the ptr
        # records.
        vd.update_ptr_extent_locations()

        return current_extent

    def _reshuffle_extents(self):
        '''
        An internal method that is one of the keys of PyIso's ability to keep
        the in-memory metadata consistent at all times.  After making any
        changes to the ISO, most API calls end up calling this method.  This
        method will run through the entire ISO, assigning extents to each of
        the pieces of the ISO that exist.  This includes the Primary Volume
        Descriptor (which is fixed at extent 16), the Boot Records (including
        El Torito), the Supplementary Volume Descriptors (including Joliet),
        the Volume Descriptor Terminators, the Version Descriptor, the Primary
        Volume Descriptor Path Table Records (little and big endian), the
        Supplementary Vollume Descriptor Path Table Records (little and big
        endian), the Primary Volume Descriptor directory records, the
        Supplementary Volume Descriptor directory records, the Rock Ridge ER
        sector, the El Torito Boot Catalog, the El Torito Initial Entry, and
        finally the data for the files.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        current_extent = self.pvd.extent_location()
        current_extent += 1

        for br in self.brs:
            br.new_extent_loc = current_extent
            current_extent += 1

        for svd in self.svds:
            svd.new_extent_loc = current_extent
            current_extent += 1

        for vdst in self.vdsts:
            vdst.new_extent_loc = current_extent
            current_extent += 1

        # Save off an extent for the version descriptor
        self.version_vd.new_extent_loc = current_extent
        current_extent += 1

        # Next up, put the path table records in the right place.
        self.pvd.path_table_location_le = current_extent
        current_extent += self.pvd.path_table_num_extents
        self.pvd.path_table_location_be = current_extent
        current_extent += self.pvd.path_table_num_extents

        for svd in self.svds:
            svd.path_table_location_le = current_extent
            current_extent += svd.path_table_num_extents
            svd.path_table_location_be = current_extent
            current_extent += svd.path_table_num_extents

        current_extent = self._reassign_vd_dirrecord_extents(self.pvd, current_extent)

        for svd in self.svds:
            current_extent = self._reassign_vd_dirrecord_extents(svd, current_extent)

        # The rock ridge "ER" sector must be after all of the directory
        # entries but before the file contents.
        if self.rock_ridge:
            self.pvd.root_directory_record().children[0].rock_ridge.ce_record.continuation_entry.new_extent_loc = current_extent
            current_extent += 1

        if self.eltorito_boot_catalog is not None:
            self.eltorito_boot_catalog.br.boot_system_use = struct.pack("=L", current_extent)
            self.eltorito_boot_catalog.dirrecord.new_extent_loc = current_extent
            current_extent += 1

            self.eltorito_boot_catalog.initial_entry_dirrecord.new_extent_loc = current_extent
            self.eltorito_boot_catalog.update_initial_entry_location(current_extent)
            current_extent += 1

        # Then we can walk the list, assigning extents to the files.
        dirs = collections.deque([self.pvd.root_directory_record()])
        while dirs:
            dir_record = dirs.popleft()
            for child in dir_record.children:
                if child.isdir:
                    if not child.file_ident == '\x00' and not child.file_ident == '\x01':
                        dirs.append(child)
                    continue

                if self.eltorito_boot_catalog:
                    if self.eltorito_boot_catalog.dirrecord == child or self.eltorito_boot_catalog.initial_entry_dirrecord == child:
                        continue

                child.new_extent_loc = current_extent
                # Equivalent to ceiling_div(child.data_length, self.pvd.log_block_size), but faster
                current_extent += -(-child.data_length // self.pvd.log_block_size)

########################### PUBLIC API #####################################
    def __init__(self):
        self._initialize()

    def new(self, interchange_level=1, sys_ident="", vol_ident="", set_size=1,
            seqnum=1, log_block_size=2048, vol_set_ident="", pub_ident_str="",
            preparer_ident_str="",
            app_ident_str="PyIso (C) 2015 Chris Lalancette", copyright_file="",
            abstract_file="", bibli_file="", vol_expire_date=None, app_use="",
            joliet=False, rock_ridge=False):
        '''
        Create a new ISO from scratch.

        Parameters:
         interchange_level - The ISO9660 interchange level to use; this dictates
                             the rules on the names of files.  Set to 1 (the most
                             conservative) by default.
         sys_ident - The system identification string to use on the new ISO.
         vol_ident - The volume identification string to use on the new ISO.
         set_size - The size of the set of ISOs this ISO is a part of.
         seqnum - The sequence number of the set of this ISO.
         log_block_size - The logical block size to use for the ISO.  While ISO9660
                          technically supports sizes other than 2048 (the default),
                          this almost certainly doesn't work.
         vol_set_ident - The volume set identification string to use on the new ISO.
         pub_ident_str - The publisher identification string to use on the new ISO.
         preparer_ident_str - The preparer identification string to use on the new ISO.
         app_ident_str - The application identification string to use on the new ISO.
         copyright_file - The name of a file at the root of the ISO to use as the
                          copyright file.
         abstract_file - The name of a file at the root of the ISO to use as the
                         abstract file.
         bibli_file - The name of a file at the root of the ISO to use as the
                      bibliographic file.
         vol_expire_date - The date that this ISO will expire at.
         app_use - Arbitrary data that the application can stuff into the primary
                   volume descriptor of this ISO.
         joliet - A boolean which controls whether to make this a Joliet ISO or not;
                  the default is False.
         rock_ridge - A boolean which controls whether to make this a Rock Ridge
                      ISO or not; the default is False.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("This object already has an ISO; either close it or create a new object")

        if interchange_level < 1 or interchange_level > 3:
            raise PyIsoException("Invalid interchange level (must be between 1 and 3)")

        self.interchange_level = interchange_level

        # First create the new PVD.
        pub_ident = FileOrTextIdentifier()
        pub_ident.new(pub_ident_str, False)
        preparer_ident = FileOrTextIdentifier()
        preparer_ident.new(preparer_ident_str, False)
        app_ident = FileOrTextIdentifier()
        app_ident.new(app_ident_str, False)

        self.pvd = PrimaryVolumeDescriptor()
        self.pvd.new(0, sys_ident, vol_ident, set_size, seqnum, log_block_size,
                     vol_set_ident, pub_ident, preparer_ident, app_ident,
                     copyright_file, abstract_file, bibli_file,
                     vol_expire_date, app_use)

        # Now that we have the PVD, make the root path table record.
        ptr = PathTableRecord()
        ptr.new_root(self.pvd.root_directory_record())
        self.pvd.add_path_table_record(ptr)

        self.joliet_vd = None
        if joliet:
            joliet_pub_ident = FileOrTextIdentifier()
            joliet_pub_ident.new(pub_ident_str.encode("utf-16_be"), False)
            joliet_preparer_ident = FileOrTextIdentifier()
            joliet_preparer_ident.new(preparer_ident_str.encode("utf-16_be"), False)
            joliet_app_ident = FileOrTextIdentifier()
            joliet_app_ident.new(app_ident_str.encode("utf-16_be"), False)

            # If the user requested Joliet, make the SVD to represent it here.
            svd = SupplementaryVolumeDescriptor()
            svd.new(0, sys_ident, vol_ident, set_size, seqnum, log_block_size,
                    vol_set_ident, joliet_pub_ident, joliet_preparer_ident,
                    joliet_app_ident, copyright_file, abstract_file,
                    bibli_file, vol_expire_date, app_use)
            self.svds = [svd]
            self.joliet_vd = svd
            ptr = PathTableRecord()
            ptr.new_root(svd.root_directory_record())
            svd.add_path_table_record(ptr)
            # Finally, make the directory entries for dot and dotdot.
            dot = DirectoryRecord()
            dot.new_dot(svd.root_directory_record(), svd.sequence_number(), False, svd.logical_block_size())
            svd.root_directory_record().add_child(dot, svd, False)

            dotdot = DirectoryRecord()
            dotdot.new_dotdot(svd.root_directory_record(), svd.sequence_number(), False, svd.logical_block_size())
            svd.root_directory_record().add_child(dotdot, svd, False)

            additional_size = svd.logical_block_size() + 2*svd.logical_block_size() + 2*svd.logical_block_size() + svd.logical_block_size()
            # Now that we have added joliet, we need to add the new space to the
            # PVD.  Here, we add one extent for the SVD itself, 2 for the little
            # endian path table records, 2 for the big endian path table
            # records, and one for the root directory record.
            self.pvd.add_to_space_size(additional_size)
            # And we add the same amount of space to the SVD.
            svd.add_to_space_size(additional_size)

        # Also make the volume descriptor set terminator.
        vdst = VolumeDescriptorSetTerminator()
        vdst.new()
        self.vdsts = [vdst]

        self.version_vd = VersionVolumeDescriptor()
        self.version_vd.new()

        # Finally, make the directory entries for dot and dotdot.
        dot = DirectoryRecord()
        dot.new_dot(self.pvd.root_directory_record(), self.pvd.sequence_number(), rock_ridge, self.pvd.logical_block_size())
        self.pvd.root_directory_record().add_child(dot, self.pvd, False)

        dotdot = DirectoryRecord()
        dotdot.new_dotdot(self.pvd.root_directory_record(), self.pvd.sequence_number(), rock_ridge, self.pvd.logical_block_size())
        self.pvd.root_directory_record().add_child(dotdot, self.pvd, False)

        self.rock_ridge = rock_ridge
        if self.rock_ridge:
            self.pvd.add_to_space_size(self.pvd.logical_block_size())
            if joliet:
                self.joliet_vd.add_to_space_size(self.joliet_vd.logical_block_size())

        self._reshuffle_extents()

        self.initialized = True

    def open(self, fp):
        '''
        Open up an existing ISO for inspection and modification.  Note that the
        file object passed in here must stay open for the lifetime of this
        object, as the PyIso class uses it internally to do writing and reading
        operations.

        Parameters:
         fp - The file object containing the ISO to open up.
        Returns:
         Nothing.
        '''
        if self.initialized:
            raise PyIsoException("This object already has an ISO; either close it or create a new object")

        self.cdfp = fp

        # Get the Primary Volume Descriptor (pvd), the set of Supplementary
        # Volume Descriptors (svds), the set of Volume Partition
        # Descriptors (vpds), the set of Boot Records (brs), and the set of
        # Volume Descriptor Set Terminators (vdsts)
        pvds, self.svds, self.vpds, self.brs, self.vdsts = self._parse_volume_descriptors()
        if len(pvds) != 1:
            raise PyIsoException("Valid ISO9660 filesystems have one and only one Primary Volume Descriptors")
        if len(self.vdsts) < 1:
            raise PyIsoException("Valid ISO9660 filesystems must have at least one Volume Descriptor Set Terminators")

        self.pvd = pvds[0]

        old = self.cdfp.tell()
        self.cdfp.seek(0)
        mbr = self.cdfp.read(512)
        if mbr[0:2] == '\x33\xed':
            # All isolinux isohdpfx.bin files start with 0x33 0xed (the x86
            # instruction for xor %bp, %bp).  Therefore, if we see that we know
            # we have a valid isohybrid, so parse that.
            self.isohybrid_mbr = IsoHybrid()
            self.isohybrid_mbr.parse(mbr)
        self.cdfp.seek(old)

        for br in self.brs:
            self._check_and_parse_eltorito(br, self.pvd.logical_block_size())

        self.version_vd = VersionVolumeDescriptor()
        self.version_vd.parse(self.vdsts[0].extent_location() + 1)

        # Now that we have the PVD, parse the Path Tables according to Ecma-119
        # section 9.4.  What we really want is a single representation of the
        # path table records, so we only place the little endian path table
        # records into the PVD class.  However, we want to ensure that the
        # big endian versions agree with the little endian ones (to make sure
        # it is a valid ISO).  To do this we collect the big endian records
        # into a sorted list (to mimic what the list is stored as in the PVD),
        # and then compare them at the end.

        # Little Endian first
        self._parse_path_table(self.pvd, self.pvd.path_table_location_le,
                               self._little_endian_path_table)

        # Big Endian next.
        self.tmp_be_path_table_records = []
        self._parse_path_table(self.pvd, self.pvd.path_table_location_be,
                               self._big_endian_path_table)

        for index,ptr in enumerate(self.tmp_be_path_table_records):
            if not self.pvd.path_table_record_be_equal_to_le(index, ptr):
                raise PyIsoException("Little-endian and big-endian path table records do not agree")

        # OK, so now that we have the PVD, we start at its root directory
        # record and find all of the files
        self.interchange_level = self._walk_directories(self.pvd, True)

        # The PVD is finished.  Now look to see if we need to parse the SVD.
        self.joliet_vd = None
        for svd in self.svds:
            if svd.joliet:
                if self.joliet_vd is not None:
                    raise PyIsoException("Only a single Joliet SVD is supported")
                self.joliet_vd = svd

                self._parse_path_table(svd, svd.path_table_location_le,
                                       self._little_endian_path_table)

                self._parse_path_table(svd, svd.path_table_location_be,
                                       self._big_endian_path_table)

                self._walk_directories(svd, False)

        self.initialized = True

    def print_tree(self):
        '''
        Print out the tree.  This is useful for debugging.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This object is not yet initialized; call either open() or new() to create an ISO")
        print("%s (extent %d)" % (self.pvd.root_directory_record().file_identifier(), self.pvd.root_directory_record().extent_location()))

        dirs = collections.deque([(self.pvd.root_directory_record(), 0)])
        visited = set()
        while dirs:
            dir_record,depth = dirs.pop()
            if dir_record not in visited:
                visited.add(dir_record)
                for child in dir_record.children:
                    if child.is_dot() or child.is_dotdot():
                        continue
                    if child not in visited:
                        dirs.append((child, depth+1))
                print("%s%s (extent %d)" % ('    '*depth, dir_record.file_identifier(), dir_record.extent_location()))

    def get_and_write(self, iso_path, outfp, blocksize=8192):
        """
        Fetch a single file from the ISO and write it out to the file object.

        Parameters:
         iso_path - The absolute path to the file to get data from.
         outfp - The file object to write data to.
         blocksize - The blocksize to use when copying data; the default is 8192.
        Returns:
         Nothing.
        """
        if not self.initialized:
            raise PyIsoException("This object is not yet initialized; call either open() or new() to create an ISO")

        try_iso9660 = True
        if self.joliet_vd is not None:
            try:
                found_record,index = self._find_record(self.joliet_vd, iso_path, 'utf-16_be')
                try_iso9660 = False
            except PyIsoException:
                pass

        if try_iso9660:
            found_record,index = self._find_record(self.pvd, iso_path)
            if found_record.rock_ridge is not None:
                if found_record.rock_ridge.is_symlink():
                    # If this Rock Ridge record is a symlink, it has no data
                    # associated with it, so it makes no sense to try and get the
                    # data.  In theory, we could follow the symlink to the
                    # the appropriate place and get the data of the thing it points
                    # to.  However, the symlinks are allowed to point *outside* of
                    # this ISO, so its really not clear that this is something we
                    # want to do.  For now we make the user follow the symlink
                    # themselves if they want to get the data.  We can revisit this
                    # decision in the future if we need to.
                    raise PyIsoException("Symlinks have no data associated with them")

        data_fp,data_length = found_record.open_data(self.pvd.logical_block_size())

        copy_data(data_length, blocksize, data_fp, outfp)

    def write(self, outfp, blocksize=8192, progress_cb=None):
        '''
        Write a properly formatted ISO out to the file object passed in.  This
        also goes by the name of "mastering".

        Parameters:
         outfp - The file object to write the data to.
         blocksize - The blocksize to use when copying data; set to 8192 by default.
         progress_cb - If not None, a function to call as the write call does its
                       work.  The callback function must have a signature of:
                       def func(done, total).
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This object is not yet initialized; call either open() or new() to create an ISO")

        outfp.seek(0)

        if progress_cb is not None:
            done = 0
            total = self.pvd.space_size * self.pvd.logical_block_size()
            progress_cb(done, total)

        if self.isohybrid_mbr is not None:
            outfp.write(self.isohybrid_mbr.record(self.pvd.space_size * self.pvd.logical_block_size()))

        # Ecma-119, 6.2.1 says that the Volume Space is divided into a System
        # Area and a Data Area, where the System Area is in logical sectors 0
        # to 15, and whose contents is not specified by the standard.  Thus
        # we skip the first 16 sectors.
        outfp.seek(self.pvd.extent_location() * self.pvd.logical_block_size())

        # First write out the PVD.
        rec = self.pvd.record()
        outfp.write(rec)

        if progress_cb is not None:
            done += len(rec)
            progress_cb(done, total)

        # Next write out the boot records.
        for br in self.brs:
            outfp.seek(br.extent_location() * self.pvd.logical_block_size())
            rec = br.record()
            outfp.write(rec)

            if progress_cb is not None:
                done += len(rec)
                progress_cb(done, total)

        # Next we write out the SVDs.
        for svd in self.svds:
            outfp.seek(svd.extent_location() * self.pvd.logical_block_size())
            rec = svd.record()
            outfp.write(rec)

            if progress_cb is not None:
                done += len(rec)
                progress_cb(done, total)

        # Next we write out the Volume Descriptor Terminators.
        for vdst in self.vdsts:
            outfp.seek(vdst.extent_location() * self.pvd.logical_block_size())
            rec = vdst.record()
            outfp.write(rec)

            if progress_cb is not None:
                done += len(rec)
                progress_cb(done, total)

        # Next we write out the version block.
        # FIXME: In genisoimage, write.c:vers_write(), this "version descriptor"
        # is written out with the exact command line used to create the ISO
        # (if in debug mode, otherwise it is all zero).  However, there is no
        # mention of this in any of the specifications I've read so far.  Where
        # does it come from?
        if self.version_vd is not None:
            outfp.seek(self.version_vd.extent_location() * self.pvd.logical_block_size())
            rec = self.version_vd.record(self.pvd.logical_block_size())
            outfp.write(rec)

            if progress_cb is not None:
                done += len(rec)
                progress_cb(done, total)

        # Next we write out the Path Table Records, both in Little Endian and
        # Big-Endian formats.  We do this within the same loop, seeking back
        # and forth as necessary.
        le_offset = 0
        be_offset = 0
        for record in self.pvd.path_table_records:
            outfp.seek(self.pvd.path_table_location_le * self.pvd.logical_block_size() + le_offset)
            ret = record.record_little_endian()
            outfp.write(ret)
            le_offset += len(ret)

            outfp.seek(self.pvd.path_table_location_be * self.pvd.logical_block_size() + be_offset)
            ret = record.record_big_endian()
            outfp.write(ret)
            be_offset += len(ret)

        # Once we are finished with the loop, we need to pad out the Big
        # Endian version.  The Little Endian one was already properly padded
        # by the mere fact that we wrote things for the Big Endian version
        # in the right place.
        outfp.write(pad(be_offset, 4096))

        if progress_cb is not None:
            done += self.pvd.path_table_num_extents * 2 * self.pvd.logical_block_size()
            progress_cb(done, total)

        # Now we write out the path table records for any SVDs.
        for svd in self.svds:
            le_offset = 0
            be_offset = 0
            for record in svd.path_table_records:
                outfp.seek(svd.path_table_location_le * svd.logical_block_size() + le_offset)
                ret = record.record_little_endian()
                outfp.write(ret)
                le_offset += len(ret)

                outfp.seek(svd.path_table_location_be * svd.logical_block_size() + be_offset)
                ret = record.record_big_endian()
                outfp.write(ret)
                be_offset += len(ret)

            # Once we are finished with the loop, we need to pad out the Big
            # Endian version.  The Little Endian one was already properly padded
            # by the mere fact that we wrote things for the Big Endian version
            # in the right place.
            outfp.write(pad(be_offset, 4096))

            if progress_cb is not None:
                done += svd.path_table_num_extents * 2 * svd.logical_block_size()
                progress_cb(done, total)

        # Now we need to write out the actual files.  Note that in many cases,
        # we haven't yet read the file out of the original, so we need to do
        # that here.
        dirs = collections.deque([self.pvd.root_directory_record()])
        while dirs:
            curr = dirs.popleft()
            curr_dirrecord_offset = 0
            if progress_cb is not None and curr.is_dir():
                done += curr.file_length()
                progress_cb(done, total)

            for child in curr.children:
                # Now matter what type the child is, we need to first write out
                # the directory record entry.
                dir_extent = child.parent.extent_location()

                outfp.seek(dir_extent * self.pvd.logical_block_size() + curr_dirrecord_offset)
                # Now write out the child
                recstr = child.record()
                outfp.write(recstr)
                curr_dirrecord_offset += len(recstr)

                if child.rock_ridge is not None and child.rock_ridge.ce_record is not None:
                    # The child has a continue block, so write it out here.
                    offset = child.rock_ridge.ce_record.continuation_entry.offset()
                    outfp.seek(child.rock_ridge.ce_record.continuation_entry.extent_location() * self.pvd.logical_block_size() + offset)
                    tmp_start = outfp.tell()
                    rec = child.rock_ridge.ce_record.continuation_entry.record()
                    outfp.write(rec)
                    if offset == 0:
                        outfp.write(pad(len(rec), self.pvd.logical_block_size()))
                        if progress_cb is not None:
                            done += outfp.tell() - tmp_start
                            progress_cb(done, total)

                if child.is_dir():
                    # If the child is a directory, and is not dot or dotdot, we
                    # want to descend into it to look at the children.
                    if not child.is_dot() and not child.is_dotdot():
                        dirs.append(child)
                    outfp.write(pad(outfp.tell(), self.pvd.logical_block_size()))
                elif child.data_length > 0:
                    # If the child is a file, then we need to write the data to
                    # the output file.
                    data_fp,data_length = child.open_data(self.pvd.logical_block_size())
                    outfp.seek(child.extent_location() * self.pvd.logical_block_size())
                    tmp_start = outfp.tell()
                    copy_data(data_length, blocksize, data_fp, outfp)
                    outfp.write(pad(data_length, self.pvd.logical_block_size()))

                    if progress_cb is not None:
                        done += outfp.tell() - tmp_start
                        progress_cb(done, total)

        for svd in self.svds:
            dirs = collections.deque([svd.root_directory_record()])
            while dirs:
                curr = dirs.popleft()
                curr_dirrecord_offset = 0
                if progress_cb is not None and curr.is_dir():
                    done += curr.file_length()
                    progress_cb(done, total)

                for child in curr.children:
                    # Now matter what type the child is, we need to first write
                    # out the directory record entry.
                    dir_extent = child.parent.extent_location()

                    outfp.seek(dir_extent * svd.logical_block_size() + curr_dirrecord_offset)
                    # Now write out the child
                    recstr = child.record()
                    outfp.write(recstr)
                    curr_dirrecord_offset += len(recstr)

                    if child.is_dir():
                        # If the child is a directory, and is not dot or dotdot,
                        # we want to descend into it to look at the children.
                        if not child.is_dot() and not child.is_dotdot():
                            dirs.append(child)
                        outfp.write(pad(outfp.tell(), svd.logical_block_size()))

        outfp.truncate(self.pvd.space_size * self.pvd.logical_block_size())

        if self.isohybrid_mbr is not None:
            outfp.seek(0, os.SEEK_END)
            outfp.write(self.isohybrid_mbr.record_padding(self.pvd.space_size * self.pvd.logical_block_size()))

        if progress_cb is not None:
            outfp.seek(0, os.SEEK_END)
            progress_cb(outfp.tell(), total)

    def add_fp(self, fp, length, iso_path, rr_path=None, joliet_path=None):
        '''
        Add a file to the ISO.  If the ISO contains Joliet or
        RockRidge, then a Joliet name and/or a RockRidge name must also be
        provided.  Note that the caller must ensure that the file remains open
        for the lifetime of the ISO object, as the PyIso class uses the file
        descriptor internally when writing (mastering) the ISO.

        Parameters:
         fp - The file object to use for the contents of the new file.
         length - The length of the data for the new file.
         iso_path - The ISO9660 absolute path to the file destination on the ISO.
         rr_path - The Rock Ridge absolute path to the file destination on
                       the ISO.
         joliet_path - The Joliet absolute path to the file destination on the ISO.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This object is not yet initialized; call either open() or new() to create an ISO")

        rr_name = None
        if self.rock_ridge:
            if rr_path is None:
                raise PyIsoException("A rock ridge path must be passed for a rock-ridge ISO")
            splitpath = rr_path.split('/')
            rr_name = splitpath[-1]
        else:
            if rr_path is not None:
                raise PyIsoException("A rock ridge path can only be specified for a rock-ridge ISO")

        if self.joliet_vd is not None:
            if joliet_path is None:
                raise PyIsoException("A Joliet path must be passed for a Joliet ISO")
        else:
            if joliet_path is not None:
                raise PyIsoException("A Joliet path can only be specified for a Joliet ISO")

        (name, parent) = self._name_and_parent_from_path(iso_path)

        check_iso9660_filename(name, self.interchange_level)

        rec = DirectoryRecord()
        rec.new_fp(fp, length, name, parent, self.pvd.sequence_number(), self.rock_ridge, rr_name)
        parent.add_child(rec, self.pvd, False)
        self.pvd.add_entry(length)

        if self.joliet_vd is not None:
            (joliet_name, joliet_parent) = self._joliet_name_and_parent_from_path(joliet_path)

            joliet_name = joliet_name.encode('utf-16_be')

            joliet_rec = DirectoryRecord()
            joliet_rec.new_fp(fp, length, joliet_name, joliet_parent, self.joliet_vd.sequence_number(), False, None)
            joliet_parent.add_child(joliet_rec, self.joliet_vd, False)
            self.joliet_vd.add_entry(length)

        self._reshuffle_extents()

        if self.joliet_vd is not None:
            # If we are doing Joliet, then we must update the joliet record with
            # the new extent location *after* having done the reshuffle.
            joliet_rec.new_extent_loc = rec.new_extent_loc

        # This needs to be *after* reshuffle_extents() so that the continuation
        # entry offsets are computed properly.
        if rec.rock_ridge is not None and rec.rock_ridge.ce_record is not None and rec.rock_ridge.ce_record.continuation_entry.continue_offset == 0:
            self.pvd.add_to_space_size(self.pvd.logical_block_size())

    def add_directory(self, iso_path, rr_path=None, joliet_path=None):
        '''
        Add a directory to the ISO.  If the ISO contains Joliet or RockRidge (or
        both), then a Joliet name and/or a RockRidge name must also be provided.

        Parameters:
         iso_path - The ISO9660 absolute path to use for the directory.
         rr_path - The Rock Ridge absolute path to use for the directory.
         joliet_path - The Joliet absolute path to use for the directory.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This object is not yet initialized; call either open() or new() to create an ISO")

        rr_name = None
        if self.rock_ridge:
            if rr_path is None:
                raise PyIsoException("A rock ridge path must be passed for a rock-ridge ISO")
            splitpath = rr_path.split('/')
            rr_name = splitpath[-1]
        else:
            if rr_path is not None:
                raise PyIsoException("A rock ridge path can only be specified for a rock-ridge ISO")

        if self.joliet_vd is not None:
            if joliet_path is None:
                raise PyIsoException("A Joliet path must be passed for a Joliet ISO")
        else:
            if joliet_path is not None:
                raise PyIsoException("A Joliet path can only be specified for a Joliet ISO")

        (name, parent) = self._name_and_parent_from_path(iso_path)

        check_iso9660_directory(name, self.interchange_level)

        rec = DirectoryRecord()
        rec.new_dir(name, parent, self.pvd.sequence_number(), self.rock_ridge, rr_name, self.pvd.logical_block_size())
        parent.add_child(rec, self.pvd, False)

        dot = DirectoryRecord()
        dot.new_dot(rec, self.pvd.sequence_number(), self.rock_ridge, self.pvd.logical_block_size())
        rec.add_child(dot, self.pvd, False)

        dotdot = DirectoryRecord()
        dotdot.new_dotdot(rec, self.pvd.sequence_number(), self.rock_ridge, self.pvd.logical_block_size())
        rec.add_child(dotdot, self.pvd, False)

        self.pvd.add_entry(self.pvd.logical_block_size(),
                           PathTableRecord.record_length(len(name)))

        # We always need to add an entry to the path table record
        ptr = PathTableRecord()
        ptr.new_dir(name, rec, self.pvd.find_parent_dirnum(parent))

        self.pvd.add_path_table_record(ptr)

        if self.joliet_vd is not None:
            (joliet_name, joliet_parent) = self._joliet_name_and_parent_from_path(joliet_path)

            joliet_name = joliet_name.encode('utf-16_be')
            rec = DirectoryRecord()
            rec.new_dir(joliet_name, joliet_parent, self.joliet_vd.sequence_number(), False, None, self.joliet_vd.logical_block_size())
            joliet_parent.add_child(rec, self.joliet_vd, False)

            dot = DirectoryRecord()
            dot.new_dot(rec, self.joliet_vd.sequence_number(), False, self.joliet_vd.logical_block_size())
            rec.add_child(dot, self.joliet_vd, False)

            dotdot = DirectoryRecord()
            dotdot.new_dotdot(rec, self.joliet_vd.sequence_number(), False, self.joliet_vd.logical_block_size())
            rec.add_child(dotdot, self.joliet_vd, False)

            self.joliet_vd.add_entry(self.joliet_vd.logical_block_size(),
                                     PathTableRecord.record_length(len(joliet_name)))

            # We always need to add an entry to the path table record
            ptr = PathTableRecord()
            ptr.new_dir(joliet_name, rec, self.joliet_vd.find_parent_dirnum(joliet_parent))

            self.joliet_vd.add_path_table_record(ptr)

            self.pvd.add_to_space_size(self.pvd.logical_block_size())

            self.joliet_vd.add_to_space_size(self.joliet_vd.logical_block_size())

        self._reshuffle_extents()

    def rm_file(self, iso_path, rr_path=None):
        '''
        Remove a file from the ISO.

        Parameters:
         iso_path - The path to the file to remove.
         rr_path - The Rock Ridge path to the file to remove.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This object is not yet initialized; call either open() or new() to create an ISO")

        if iso_path[0] != '/':
            raise PyIsoException("Must be a path starting with /")

        child,index = self._find_record(self.pvd, iso_path)

        # FIXME: what if this is a joliet file?

        if not child.is_file():
            raise PyIsoException("Cannot remove a directory with rm_file (try rm_directory instead(")

        child.parent.remove_child(child, index, self.pvd)

        self.pvd.remove_entry(child.file_length())
        if self.joliet_vd is not None:
            self.joliet_vd.remove_entry(child.file_length())

        self._reshuffle_extents()

    def rm_directory(self, iso_path):
        '''
        Remove a directory from the ISO.

        Parameters:
         iso_path - The path to the directory to remove.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This object is not yet initialized; call either open() or new() to create an ISO")

        if iso_path == '/':
            raise PyIsoException("Cannot remove base directory")

        child,index = self._find_record(self.pvd, iso_path)

        # FIXME: what if this is a joliet directory?

        if not child.is_dir():
            raise PyIsoException("Cannot remove a file with rm_directory (try rm_file instead)")

        for c in child.children:
            if c.is_dot() or c.is_dotdot():
                continue
            raise PyIsoException("Directory must be empty to use rm_directory")

        child.parent.remove_child(child, index, self.pvd)

        self.pvd.remove_entry(child.file_length(), child.file_ident)
        self._reshuffle_extents()

    def add_eltorito(self, bootfile_path, bootcatfile="/BOOT.CAT;1",
                     rr_bootcatfile="boot.cat", joliet_bootcatfile="/boot.cat",
                     boot_load_size=None):
        '''
        Add an El Torito Boot Record, and associated files, to the ISO.  The
        file that will be used as the bootfile must be passed into this function
        and must already be present on the ISO.

        Parameters:
         bootfile_path - The file to use as the boot file; it must already exist on
                         this ISO.
         bootcatfile - The fake file to use as the boot catalog entry; set to
                       BOOT.CAT;1 by default.
         rr_bootcatfile - The Rock Ridge name for the fake file to use as the boot
                          catalog entry; set to "boot.cat" by default.
         joliet_bootcatfile - The Joliet name for the fake file to use as the boot
                              catalog entry; set to "boot.cat" by default.
         boot_load_size - The number of sectors to use for the boot entry; if set
                          to None (the default), the number of sectors will be
                          calculated.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This object is not yet initialized; call either open() or new() to create an ISO")

        if self.eltorito_boot_catalog is not None:
            raise PyIsoException("This ISO already has an El Torito Boot Record")

        # In order to add an El Torito boot, we need to do the following:
        # 1.  Find the boot file record (which must already exist).
        # 2.  Construct a BootCatalog.
        # 3.  Add the BootCatalog file to the filesystem.  When this step is
        #     over, we will know the extent that the file lives at.
        # 4.  Add the boot record to the ISO.

        # Step 1.
        child,index = self._find_record(self.pvd, bootfile_path)

        if boot_load_size is None:
            sector_count = ceiling_div(child.file_length(), self.pvd.logical_block_size()) * self.pvd.logical_block_size()/512
        else:
            sector_count = boot_load_size

        # Step 2.
        br = BootRecord()
        br.new("EL TORITO SPECIFICATION")
        self.brs.append(br)

        # Step 3.
        self.eltorito_boot_catalog = EltoritoBootCatalog(br)
        self.eltorito_boot_catalog.new(br, sector_count)
        self.eltorito_boot_catalog.set_initial_entry_dirrecord(child)

        # Step 4.
        fp = StringIO.StringIO()
        fp.write(self.eltorito_boot_catalog.record())
        fp.seek(0)
        (name, parent) = self._name_and_parent_from_path(bootcatfile)

        check_iso9660_filename(name, self.interchange_level)

        bootcat_dirrecord = DirectoryRecord()
        length = len(fp.getvalue())
        bootcat_dirrecord.new_fp(fp, length, name, parent,
                                 self.pvd.sequence_number(), self.rock_ridge,
                                 rr_bootcatfile)
        parent.add_child(bootcat_dirrecord, self.pvd, False)
        self.pvd.add_entry(length)
        if bootcat_dirrecord.rock_ridge is not None and bootcat_dirrecord.rock_ridge.ce_record is not None:
            self.pvd.add_to_space_size(self.pvd.logical_block_size())

        self.eltorito_boot_catalog.set_dirrecord(bootcat_dirrecord)

        if self.joliet_vd is not None:
            (joliet_name, joliet_parent) = self._joliet_name_and_parent_from_path(joliet_bootcatfile)

            joliet_name = joliet_name.encode('utf-16_be')

            joliet_rec = DirectoryRecord()
            joliet_rec.new_fp(fp, length, joliet_name, joliet_parent, self.joliet_vd.sequence_number(), False, None)
            joliet_parent.add_child(joliet_rec, self.joliet_vd, False)
            self.joliet_vd.add_entry(length)
            self.joliet_vd.add_to_space_size(self.joliet_vd.logical_block_size())

        self.pvd.add_to_space_size(self.pvd.logical_block_size())
        self._reshuffle_extents()

        if self.joliet_vd is not None:
            # If we are doing Joliet, then we must update the joliet record with
            # the new extent location *after* having done the reshuffle.
            joliet_rec.new_extent_loc = bootcat_dirrecord.new_extent_loc

    def rm_eltorito(self):
        '''
        Remove the El Torito boot record (and associated files) from the ISO.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This object is not yet initialized; call either open() or new() to create an ISO")

        if self.eltorito_boot_catalog is None:
            raise PyIsoException("This ISO doesn't have an El Torito Boot Record")

        eltorito_index = None
        for index,br in enumerate(self.brs):
            if br.boot_system_identifier == "{:\x00<32}".format("EL TORITO SPECIFICATION"):
                eltorito_index = index
                break

        if eltorito_index is None:
            # There was a boot catalog, but no corresponding boot record.  This
            # should never happen.
            raise PyIsoException("El Torito boot catalog found with no corresponding boot record")

        extent, = struct.unpack("=L", br.boot_system_use[:4])

        del self.brs[eltorito_index]

        self.eltorito_boot_catalog = None

        self.pvd.remove_from_space_size(self.pvd.logical_block_size())
        if self.joliet_vd is not None:
            self.joliet_vd.remove_from_space_size(self.joliet_vd.logical_block_size())

        # Search through the filesystem, looking for the file that matches the
        # extent that the boot catalog lives at.
        dirs = [self.pvd.root_directory_record()]
        while dirs:
            curr = dirs.pop(0)
            for index,child in enumerate(curr.children):
                if child.is_dot() or child.is_dotdot():
                    continue

                if child.is_dir():
                    dirs.append(child)
                else:
                    if child.extent_location() == extent:
                        # We found the child
                        child.parent.remove_child(child, index, self.pvd)
                        self.pvd.remove_entry(child.file_length())
                        if self.joliet_vd is not None:
                            self.joliet_vd.remove_entry(child.file_length())
                        self._reshuffle_extents()
                        return

        raise PyIsoException("Could not find boot catalog file to remove!")

    def add_symlink(self, symlink_path, rr_symlink_name, rr_path):
        '''
        Add a symlink from rr_symlink_name to the rr_path.  The non-RR name
        of the symlink must also be provided.

        Parameters:
         symlink_path - The ISO9660 name of the symlink itself on the ISO.
         rr_symlink_name - The Rock Ridge name of the symlink itself on the ISO.
         rr_path - The Rock Ridge name of the entry on the ISO that the symlink
                       points to.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This object is not yet initialized; call either open() or new() to create an ISO")

        if not self.rock_ridge:
            raise PyIsoException("Can only add symlinks to a Rock Ridge ISO")

        (name, parent) = self._name_and_parent_from_path(symlink_path)

        if rr_path[0] == '/':
            raise PyIsoException("Rock Ridge symlink target path must be relative")

        rec = DirectoryRecord()
        rec.new_symlink(name, parent, rr_path, self.pvd.sequence_number(),
                        rr_symlink_name)
        parent.add_child(rec, self.pvd, False)
        self._reshuffle_extents()

    def list_dir(self, iso_path):
        '''
        Generate a list of all of the file/directory objects in the specified
        location on the ISO.

        Parameters:
         iso_path - The path on the ISO to look up information for.
        Yields:
         Children of this path.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This object is not yet initialized; call either open() or new() to create an ISO")

        rec,index = self._find_record(self.pvd, iso_path)

        if not rec.is_dir():
            raise PyIsoException("Record is not a directory!")

        for child in rec.children:
            yield child

    def get_entry(self, iso_path):
        '''
        Get information about whether a particular iso_path is a directory or a
        regular file.

        Parameters:
         iso_path - The path on the ISO to look up information for.
        Returns:
         A DirectoryRecord object representing the path.
        '''
        if not self.initialized:
            raise PyIsoException("This object is not yet initialized; call either open() or new() to create an ISO")

        rec,index = self._find_record(self.pvd, iso_path)

        return rec

    def add_isohybrid(self, isohybrid_fp, part_entry=1, mbr_id=None,
                      part_offset=0, geometry_sectors=32, geometry_heads=64,
                      part_type=0x17):
        '''
        Make an ISO a "hybrid", which means that it can be booted either from a
        CD or from more traditional media (like a USB stick).  This requires
        passing in a file object that contains a bootable image, and has a
        certain signature (if using syslinux, this generally means the
        isohdpfx.bin files).

        Paramters:
         isohybrid_fp - A file object which points to the bootable image.
         part_entry - The partition entry to use; one by default.
         mbr_id - The mbr_id to use.  If set to None (the default), a random one
                  will be generated.
         part_offset - The partition offset to use; zero by default.
         geometry_sectors - The number of sectors to assign; thirty-two by default.
         geometry_heads - The number of heads to assign; sixty-four by default.
         part_type - The partition type to assign; twenty-three by default.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This object is not yet initialized; call either open() or new() to create an ISO")

        isohybrid_fp.seek(0, os.SEEK_END)
        size = isohybrid_fp.tell()
        if size != 432:
            raise PyIsoException("The isohybrid file must be exactly 432 bytes")

        if self.eltorito_boot_catalog is None:
            raise PyIsoException("The ISO must have an El Torito Boot Record to add isohybrid support")

        if self.eltorito_boot_catalog.initial_entry.sector_count != 4:
            raise PyIsoException("El Torito Boot Catalog sector count must be 4 (was actually 0x%x)" % (self.eltorito_boot_catalog.initial_entry.sector_count))

        # Now check that the eltorito boot file contains the appropriate
        # signature (offset 0x40, '\xFB\xC0\x78\x70')
        bootfile_dirrecord = self.eltorito_boot_catalog.initial_entry_dirrecord
        data_fp,data_length = bootfile_dirrecord.open_data(self.pvd.logical_block_size())
        data_fp.seek(0x40, os.SEEK_CUR)
        signature = data_fp.read(4)
        if signature != '\xfb\xc0\x78\x70':
            raise PyIsoException("Invalid signature on boot file for iso hybrid")

        isohybrid_fp.seek(0)
        self.isohybrid_mbr = IsoHybrid()
        self.isohybrid_mbr.new(isohybrid_fp.read(432),
                               self.eltorito_boot_catalog.initial_entry.load_rba,
                               part_entry,
                               mbr_id,
                               part_offset,
                               geometry_sectors,
                               geometry_heads,
                               part_type)

    def rm_isohybrid(self):
        '''
        Remove the "hybridization" of an ISO, making it a traditional ISO again.
        This means the ISO will no longer be able to be copied and booted off
        of traditional media (like USB sticks).

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This object is not yet initialized; call either open() or new() to create an ISO")

        self.isohybrid_mbr = None

    def close(self):
        '''
        Close a previously opened ISO, and re-initialize the object to the
        defaults.  After this call the object can be re-used for manipulation
        of another ISO.

        Parameters:
         None.
        Returns:
         Nothing.
        '''
        if not self.initialized:
            raise PyIsoException("This object is not yet initialized; call either open() or new() to create an ISO")

        # now that we are closed, re-initialize everything
        self._initialize()

    # FIXME: we might need an API call to manipulate permission bits on
    # individual files.
    # FIXME: it is possible, though possibly complicated, to add
    # Joliet/RockRidge to an ISO that doesn't currently have it.  We may want
    # to investigate adding this support.
