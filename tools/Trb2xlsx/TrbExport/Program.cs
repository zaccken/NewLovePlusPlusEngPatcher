using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.IO;
using OfficeOpenXml;

namespace Trb2xlsx
{
    class Program
    {
        private static string[] lookup;
        static void Main(string[] args)
        {
            try
            {
                if (args.Length > 0)
                {
                    string FileName = args[0];
                    if (File.Exists(FileName))
                    {
                        lookup = File.ReadAllLines("lookup.txt");
                        if (FileName.EndsWith(".xlsx"))
                            Compress(FileName);
                        else
                            Decompress(FileName);
                        Console.WriteLine("Done!");
                    }

                }
            }
            catch (Exception ex)
            {
                Console.WriteLine(ex.Message);
            }

        }

        private static void Compress(string fileName)
        {
            List<byte> stri = new List<byte>();
            List<byte> strb = new List<byte>();
            List<byte> trb = new List<byte>();
            FileInfo newFile = new FileInfo(fileName);
            newFile.CopyTo(newFile.FullName + ".xlsx", true);
            newFile = new FileInfo(newFile.FullName + ".xlsx");
            if (newFile.Exists)
                using (ExcelPackage package = new ExcelPackage(newFile))
                {
                    ExcelWorksheet workSheet = package.Workbook.Worksheets[1];
                    var start = workSheet.Dimension.Start;
                    var end = workSheet.Dimension.End;
                    for (int row = start.Row; row <= end.Row; row++)
                    {
                        byte[] buffer;
                        buffer = Encoding.UTF8.GetBytes(workSheet.Cells[row, 3].Text.Replace("\r", "").Replace('◙', (char)0x0A));
                        int length = Encoding.UTF8.GetBytes(workSheet.Cells[row, 3].Text.Replace("\r", "").Replace('◙', (char)0x0A)).Length;
                        //int length = workSheet.Cells[row, 3].Text.Replace("\r", "").Replace('◙', (char)0x0A).Count()*3;
                        ushort flag = 1;
                        if (workSheet.Cells[row, 3].Text == "" && workSheet.Cells[row, 4].Text == "")
                        {
                            buffer = Encoding.UTF8.GetBytes(workSheet.Cells[row, 2].Text.Replace('◙', (char)0x0A));
                            length = buffer.Length;
                            if (workSheet.Cells[row, 1].Text != "1")
                            {
                                buffer = NLPGetBytes(workSheet.Cells[row, 2].Text);
                            }
                            flag = ushort.Parse(workSheet.Cells[row, 1].Text);
                        }
                        else
                        {
                            byte[] buffer2 = NLPGetBytes(workSheet.Cells[row, 3].Text.Replace("\r", "").Replace('\n', '◙'));
                            if ((buffer2.Length <= length && workSheet.Cells[row, 4].Text == "") || workSheet.Cells[row, 4].Text == "0")
                            {
                                flag = 0;
                                buffer = buffer2;

                            }
                        }
                        stri.AddRange(BitConverter.GetBytes((uint)strb.Count));
                        if (flag == 2) length = 0;
                        stri.AddRange(BitConverter.GetBytes((ushort)length));
                        stri.AddRange(BitConverter.GetBytes(flag));
                        if (flag == 2) continue;
                        strb.AddRange(buffer);
                        strb.Add(0x00);
                    }
                    var paddingsize = 4 - strb.Count % 4; //Padding
                    for (int i = 1; i <= paddingsize; i++)
                        strb.Add(0xC9);
                }


            trb.AddRange(Encoding.ASCII.GetBytes("STRI"));
            trb.AddRange(BitConverter.GetBytes((uint)stri.Count));
            trb.AddRange(stri);
            trb.AddRange(File.ReadAllBytes("cdei"));
            trb.AddRange(Encoding.ASCII.GetBytes("STRB"));
            trb.AddRange(BitConverter.GetBytes((uint)strb.Count));
            trb.AddRange(strb);
            trb.AddRange(File.ReadAllBytes("cdeb"));
            trb.AddRange(File.ReadAllBytes("conf"));
            trb.AddRange(File.ReadAllBytes("indx"));
            File.WriteAllBytes(fileName.Replace(".xlsx", ""), trb.ToArray());
            newFile.Delete();
        }
        private static string ByteArraytoHexString(byte[] data)
        {
            return BitConverter.ToString(data).Replace("-", string.Empty);
        }
        private static void Decompress(string fileName)
        {
            byte[] trb = File.ReadAllBytes(fileName);
            ByteReader Reader = new ByteReader(trb);
            string filesignature = Reader.ReadString(4);
            int strisize = Reader.ReadInt32();
            byte[] stri = Reader.ReadBytes(strisize);
            byte[] strb = new byte[0];
            for (int i = 0; i < 2; i++)
            {
                filesignature = Reader.ReadString(4);
                int size = Reader.ReadInt32();
                strb = Reader.ReadBytes(size);
            }
            Reader.Dispose();
            Reader = new ByteReader(stri);
            ByteReader strbReader = new ByteReader(strb);
            int stringindex = 0;
            short bytelength = 0;
            short flag = 0;
            FileInfo newFile = new FileInfo(fileName + ".xlsx");
            if (newFile.Exists)
                newFile.Delete();
            using (ExcelPackage package = new ExcelPackage(newFile))
            {

                ExcelWorksheet workSheet = package.Workbook.Worksheets.Add("Sheet1");

                int idx = 1;
                while (Reader.BaseStream.Position != Reader.BaseStream.Length)
                {
                    stringindex = Reader.ReadInt32();
                    bytelength = Reader.ReadInt16();
                    flag = Reader.ReadInt16();

                    string text = null;
                    if (flag != 1)
                    {
                        byte[] buffer = strbReader.ReadBytesFromIndex(stringindex, flag);
                        text = NLPGetString(buffer);
                    }
                    else
                    {
                        byte[] buffer = strbReader.ReadBytesFromIndex(stringindex);
                        text = Encoding.UTF8.GetString(buffer);
                    }

                    workSheet.Cells[idx, 1].Value = flag;
                    workSheet.Cells[idx, 2].Style.Numberformat.Format = "@";
                    workSheet.Cells[idx, 2].Value = text.Replace((char)0x0A, '◙');
                    idx++;
                }
                package.Save();
            }


        }
        private static string NLPGetString(byte[] bytes)
        {
            try
            {
                string ret = "";
                byte[] buffer = new byte[1];
                foreach (byte character in bytes)
                {
                    int charindex = 1;
                    if (buffer[0] == 0 && character >= 0x80)
                    {
                        buffer[0] = character;
                        continue;
                    }
                    if (buffer[0] >= 0x80)
                    {

                        charindex = buffer[0];
                        charindex -= 0x80;
                        charindex = charindex << 2 * 4;
                        charindex += character;
                        buffer = new byte[1];
                    }
                    else
                    {
                        charindex = character;
                    }
                    ret += lookup[charindex - 1];
                }
                return ret;
            }
            catch (Exception ex)
            {
                throw ex;
            }
        }
        private static byte[] NLPGetBytes(string word)
        {

            List<byte> buffer = new List<byte>();
            foreach (char character in word)
            {
                int charindex = Array.IndexOf(lookup, character.ToString());
                charindex++;
                if (charindex >= 0x80)
                {
                    buffer.AddRange(BitConverter.GetBytes(System.Net.IPAddress.NetworkToHostOrder((short)(charindex + 0x8000))));
                }
                else
                {
                    buffer.Add((byte)charindex);
                }
            }

            return buffer.ToArray();
        }
    }
}
